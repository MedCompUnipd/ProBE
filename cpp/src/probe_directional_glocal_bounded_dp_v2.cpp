// Production ProBE candidate pipeline.
// Stage 06C0: certified bounded directional glocal DP of NONEXACT jobs.
//
// Two alignments are computed independently for every NONEXACT pair:
//   TARGET_GLOBAL:
//     consume the complete target; candidate N/C overhangs are free.
//   CANDIDATE_GLOBAL:
//     consume the complete candidate; target N/C overhangs are free.
//
// The score-bound region contains EVERY path attaining a known lower bound.
// Thus it preserves the full-DP optimum, endpoints and predecessor tie order.
// Poor lower bounds expand the region, possibly to the complete matrix.
// No candidate, residue, free end or ambiguous symbol is silently omitted.
// Literal equality scores +2, including B/B, J/J, X/X and Z/Z; every other
// aligned pair scores -1.  Gap opening is -3, extension -1.
//
// Proof for TARGET_GLOBAL, global length n, other length m, d = j - i:
// a path through (i,j) has at least a=max(i-j,0) target gaps before this
// cell and b=max((n-i)-(m-j),0) target gaps after it.  At most n-a-b
// target residues can match (+2 each); gaps cost at least -1 each.
// Therefore its complete score is <= 2*n - 3*(a+b).  A valid score L
// implies every optimal path has a+b <= floor((2*n-L)/3) = K, exactly
// -K <= d <= (m-n)+K.  CANDIDATE_GLOBAL swaps the two sequences, giving
// (m-n)-K <= d <= K for K=floor((2*m-L)/3).
// All states and all equality cases inside this region are retained.
// Scores L come only from explicit, valid alignments, never from a heuristic
// upper estimate.  Removed cells cannot belong to any optimal alignment.
// The original full-DP traversal and all tie rules remain unchanged.
// This program makes NO SAME_ENTITY or taxonomy decision.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#ifdef _OPENMP
#include <omp.h>
#endif

namespace {

constexpr int NEG = -1000000000;
constexpr int MATCH_SCORE = 2;
constexpr int MISMATCH_SCORE = -1;
constexpr int GAP_OPEN = -3;
constexpr int GAP_EXTEND = -1;

struct TargetRec {
    std::string accession;
    std::string seq;
};

struct CandidateRec {
    std::string accession;
    std::string seq;
};

struct Job {
    std::string release;
    std::string target_id;
    std::uint64_t rid = 0;
    std::string job_scope;
    std::string discovery_channel;
};

struct Choice {
    int score = NEG;
    std::uint8_t pred = 3;
};

struct EndPoint {
    int score = NEG;
    int i = -1;
    int j = -1;
    int state = -1;
};

struct AlignResult {
    bool valid = false;
    int score = NEG;
    int target_start = 0;
    int target_end = 0;
    int candidate_start = 0;
    int candidate_end = 0;
    std::uint64_t matches = 0;
    std::uint64_t mismatches = 0;
    std::uint64_t gap_in_target = 0;
    std::uint64_t gap_in_candidate = 0;
    std::uint64_t gap_openings = 0;
    std::uint64_t columns = 0;
    std::uint64_t cells = 0;
};

struct BandPlan {
    int lower_bound = NEG;
    int diagonal_min = 0;
    int diagonal_max = 0;
    std::uint64_t cells = 0;
    std::uint64_t full_cells = 0;
};

enum class Mode {
    TARGET_GLOBAL,
    CANDIDATE_GLOBAL
};

std::vector<std::string> split_tab(const std::string& line) {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (true) {
        const auto p = line.find('\t', start);
        if (p == std::string::npos) {
            out.emplace_back(line.substr(start));
            return out;
        }
        out.emplace_back(line.substr(start, p - start));
        start = p + 1;
    }
}

std::unordered_map<std::string, std::size_t>
header_index(const std::vector<std::string>& fields) {
    std::unordered_map<std::string, std::size_t> out;
    for (std::size_t i = 0; i < fields.size(); ++i) {
        out.emplace(fields[i], i);
    }
    return out;
}

std::size_t require_col(
    const std::unordered_map<std::string, std::size_t>& h,
    const std::string& name
) {
    const auto it = h.find(name);
    if (it == h.end()) throw std::runtime_error("missing TSV column: " + name);
    return it->second;
}

std::uint64_t parse_u64(const std::string& s, const std::string& what) {
    std::size_t pos = 0;
    const auto v = std::stoull(s, &pos);
    if (pos != s.size()) {
        throw std::runtime_error("invalid " + what + ": " + s);
    }
    return v;
}

Choice best3(
    int s0, std::uint8_t p0,
    int s1, std::uint8_t p1,
    int s2, std::uint8_t p2
) {
    Choice c{s0, p0};
    if (s1 > c.score) c = {s1, p1};
    if (s2 > c.score) c = {s2, p2};
    return c;
}

void set_pred(std::uint8_t& byte, int state, std::uint8_t pred) {
    const int shift = state * 2;
    byte = static_cast<std::uint8_t>(
        (byte & ~(static_cast<std::uint8_t>(0x3u << shift))) |
        static_cast<std::uint8_t>((pred & 0x3u) << shift)
    );
}

std::uint8_t get_pred(std::uint8_t byte, int state) {
    return static_cast<std::uint8_t>((byte >> (state * 2)) & 0x3u);
}

bool better_endpoint(
    int score,
    int i,
    int j,
    int state,
    const EndPoint& best
) {
    if (score != best.score) return score > best.score;
    const int consumed = i + j;
    const int best_consumed = best.i + best.j;
    if (consumed != best_consumed) return consumed > best_consumed;
    if (state != best.state) return state < best.state;
    if (i != best.i) return i > best.i;
    return j > best.j;
}

int gap_score(int length) {
    return length > 0 ? GAP_OPEN + (length - 1) * GAP_EXTEND : 0;
}

int diagonal_lower_bound(
    const std::string& target,
    const std::string& candidate,
    Mode mode,
    int diagonal
) {
    const int n = static_cast<int>(target.size());
    const int m = static_cast<int>(candidate.size());
    const int i = std::max(0, -diagonal);
    const int j = std::max(0, diagonal);
    const int pairs = std::min(n - i, m - j);
    if (pairs <= 0) return NEG;
    int score = gap_score(mode == Mode::TARGET_GLOBAL ? i : j);
    for (int k = 0; k < pairs; ++k) {
        score += target[static_cast<std::size_t>(i + k)] ==
            candidate[static_cast<std::size_t>(j + k)]
            ? MATCH_SCORE : MISMATCH_SCORE;
    }
    score += gap_score(mode == Mode::TARGET_GLOBAL
        ? n - i - pairs : m - j - pairs);
    return score;
}

int prefix_suffix_lower_bound(
    const std::string& target,
    const std::string& candidate
) {
    const int n = static_cast<int>(target.size());
    const int m = static_cast<int>(candidate.size());
    int prefix = 0;
    while (prefix < std::min(n, m) &&
        target[static_cast<std::size_t>(prefix)] ==
        candidate[static_cast<std::size_t>(prefix)]) {
        ++prefix;
    }
    int suffix = 0;
    while (suffix < std::min(n, m) - prefix &&
        target[static_cast<std::size_t>(n - suffix - 1)] ==
        candidate[static_cast<std::size_t>(m - suffix - 1)]) {
        ++suffix;
    }
    const int pairs = std::min(n, m) - prefix - suffix;
    const int difference = std::max(n, m) - std::min(n, m);
    const int constant = MATCH_SCORE * (prefix + suffix) +
        gap_score(difference);
    int left = constant;
    int right = constant;
    for (int k = 0; k < pairs; ++k) {
        left += target[static_cast<std::size_t>(prefix + k)] ==
            candidate[static_cast<std::size_t>(prefix + k)]
            ? MATCH_SCORE : MISMATCH_SCORE;
        right += target[static_cast<std::size_t>(n - suffix - pairs + k)] ==
            candidate[static_cast<std::size_t>(m - suffix - pairs + k)]
            ? MATCH_SCORE : MISMATCH_SCORE;
    }
    // These alignments consume both sequences.  Penalizing an overhang that
    // could be free can only lower their score, so both are valid lower bounds
    // for either directional glocal problem.
    return std::max(left, right);
}

BandPlan plan_glocal(
    const std::string& target,
    const std::string& candidate,
    Mode mode
) {
    if (target.empty() || candidate.empty()) {
        throw std::runtime_error("empty sequence in directional glocal DP");
    }
    const auto safe_length = static_cast<std::size_t>(
        std::numeric_limits<int>::max() / 8
    );
    if (target.size() > safe_length || candidate.size() > safe_length) {
        throw std::runtime_error("sequence length exceeds score integer range");
    }
    const int n = static_cast<int>(target.size());
    const int m = static_cast<int>(candidate.size());
    const int difference = m - n;
    BandPlan plan;
    plan.lower_bound = std::max({
        diagonal_lower_bound(target, candidate, mode, 0),
        diagonal_lower_bound(target, candidate, mode, difference),
        prefix_suffix_lower_bound(target, candidate)
    });
    const int global_length = mode == Mode::TARGET_GLOBAL ? n : m;
    const int deficit = MATCH_SCORE * global_length - plan.lower_bound;
    if (deficit < 0) {
        throw std::runtime_error("invalid alignment lower bound");
    }
    const int k = deficit / (MATCH_SCORE - GAP_EXTEND);
    if (mode == Mode::TARGET_GLOBAL) {
        plan.diagonal_min = -k;
        plan.diagonal_max = difference + k;
    } else {
        plan.diagonal_min = difference - k;
        plan.diagonal_max = k;
    }
    for (int i = 0; i <= n; ++i) {
        const int low = std::max(0, i + plan.diagonal_min);
        const int high = std::min(m, i + plan.diagonal_max);
        plan.cells += static_cast<std::uint64_t>(std::max(0, high - low + 1));
    }
    plan.full_cells = static_cast<std::uint64_t>(n + 1) *
        static_cast<std::uint64_t>(m + 1);
    if (plan.cells == 0 || plan.cells > plan.full_cells) {
        throw std::runtime_error("invalid certified band size");
    }
    return plan;
}

AlignResult align_glocal_bounded(
    const std::string& target,
    const std::string& candidate,
    Mode mode,
    const BandPlan& plan
) {
    const int n = static_cast<int>(target.size());
    const int m = static_cast<int>(candidate.size());
    const auto row_low = [&](int i) {
        return std::max(0, i + plan.diagonal_min);
    };
    const auto row_high = [&](int i) {
        return std::min(m, i + plan.diagonal_max);
    };
    std::vector<std::uint64_t> row_offsets(
        static_cast<std::size_t>(n + 2), 0
    );
    for (int i = 0; i <= n; ++i) {
        const int low = row_low(i);
        const int high = row_high(i);
        row_offsets[static_cast<std::size_t>(i + 1)] =
            row_offsets[static_cast<std::size_t>(i)] +
            static_cast<std::uint64_t>(std::max(0, high - low + 1));
    }
    if (row_offsets.back() != plan.cells) {
        throw std::runtime_error("certified band cell count mismatch");
    }
    const auto trace_pos = [&](int i, int j) {
        return static_cast<std::size_t>(
            row_offsets[static_cast<std::size_t>(i)] +
            static_cast<std::uint64_t>(j - row_low(i))
        );
    };

    std::vector<std::uint8_t> trace(
        static_cast<std::size_t>(plan.cells),
        static_cast<std::uint8_t>(0x3F)
    );

    std::vector<int> prevM(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> prevX(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> prevY(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curM(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curX(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curY(static_cast<std::size_t>(m + 1), NEG);

    // Row 0: excluded boundary cells cannot reach the known lower bound.
    int previous_low = row_low(0);
    int previous_high = row_high(0);
    if (previous_low == 0) prevM[0] = 0;

    if (mode == Mode::TARGET_GLOBAL) {
        // Candidate leading overhang is free.
        for (int j = std::max(1, previous_low); j <= previous_high; ++j) {
            prevY[static_cast<std::size_t>(j)] = 0;
        }
    } else {
        // Candidate is global: its leading residues are penalized.
        for (int j = std::max(1, previous_low); j <= previous_high; ++j) {
            const std::size_t idx =
                static_cast<std::size_t>(j);
            if (j == 1) {
                prevY[idx] = GAP_OPEN;
                set_pred(trace[trace_pos(0, j)], 2, 0);
            } else {
                prevY[idx] =
                    prevY[static_cast<std::size_t>(j - 1)] + GAP_EXTEND;
                set_pred(trace[trace_pos(0, j)], 2, 2);
            }
        }
    }

    EndPoint best_end;

    for (int i = 1; i <= n; ++i) {
        const int low = row_low(i);
        const int high = row_high(i);
        if (low > high) {
            throw std::runtime_error("empty row in certified band");
        }
        std::fill(curM.begin() + low, curM.begin() + high + 1, NEG);
        std::fill(curX.begin() + low, curX.begin() + high + 1, NEG);
        std::fill(curY.begin() + low, curY.begin() + high + 1, NEG);
        const auto above = [&](const std::vector<int>& scores, int j) {
            return j >= previous_low && j <= previous_high
                ? scores[static_cast<std::size_t>(j)] : NEG;
        };

        if (low == 0 && mode == Mode::CANDIDATE_GLOBAL) {
            // Target leading overhang is free.
            curX[0] = 0;
        } else if (low == 0) {
            // Target is global: its leading residues are penalized.
            if (i == 1) {
                curX[0] = GAP_OPEN;
                set_pred(
                    trace[trace_pos(i, 0)],
                    1,
                    0
                );
            } else {
                curX[0] = prevX[0] + GAP_EXTEND;
                set_pred(
                    trace[trace_pos(i, 0)],
                    1,
                    1
                );
            }
        }

        for (int j = std::max(1, low); j <= high; ++j) {
            const auto idx = static_cast<std::size_t>(j);
            auto& tb = trace[trace_pos(i, j)];

            const auto mc = best3(
                above(prevM, j - 1), 0,
                above(prevX, j - 1), 1,
                above(prevY, j - 1), 2
            );
            if (mc.score > NEG / 2) {
                curM[idx] =
                    mc.score +
                    (target[static_cast<std::size_t>(i - 1)] ==
                     candidate[static_cast<std::size_t>(j - 1)]
                         ? MATCH_SCORE
                         : MISMATCH_SCORE);
                set_pred(tb, 0, mc.pred);
            }

            const auto xc = best3(
                above(prevX, j) > NEG / 2
                    ? above(prevX, j) + GAP_EXTEND
                    : NEG,
                1,
                above(prevM, j) > NEG / 2
                    ? above(prevM, j) + GAP_OPEN
                    : NEG,
                0,
                above(prevY, j) > NEG / 2
                    ? above(prevY, j) + GAP_OPEN
                    : NEG,
                2
            );
            curX[idx] = xc.score;
            if (xc.score > NEG / 2) set_pred(tb, 1, xc.pred);

            const auto yc = best3(
                j > low && curY[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curY[static_cast<std::size_t>(j - 1)] + GAP_EXTEND
                    : NEG,
                2,
                j > low && curM[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curM[static_cast<std::size_t>(j - 1)] + GAP_OPEN
                    : NEG,
                0,
                j > low && curX[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curX[static_cast<std::size_t>(j - 1)] + GAP_OPEN
                    : NEG,
                1
            );
            curY[idx] = yc.score;
            if (yc.score > NEG / 2) set_pred(tb, 2, yc.pred);
        }

        if (mode == Mode::TARGET_GLOBAL && i == n) {
            // Candidate trailing overhang is free: endpoint anywhere on row n.
            for (int j = low; j <= high; ++j) {
                const int scores[3] = {
                    curM[static_cast<std::size_t>(j)],
                    curX[static_cast<std::size_t>(j)],
                    curY[static_cast<std::size_t>(j)]
                };
                for (int state = 0; state < 3; ++state) {
                    if (scores[state] <= NEG / 2) continue;
                    if (better_endpoint(
                            scores[state], i, j, state, best_end
                        )) {
                        best_end = {scores[state], i, j, state};
                    }
                }
            }
        } else if (mode == Mode::CANDIDATE_GLOBAL && m >= low && m <= high) {
            // Candidate must be fully consumed: endpoint on column m.
            const int scores[3] = {
                curM[static_cast<std::size_t>(m)],
                curX[static_cast<std::size_t>(m)],
                curY[static_cast<std::size_t>(m)]
            };
            for (int state = 0; state < 3; ++state) {
                if (scores[state] <= NEG / 2) continue;
                if (better_endpoint(
                        scores[state], i, m, state, best_end
                    )) {
                    best_end = {scores[state], i, m, state};
                }
            }
        }

        prevM.swap(curM);
        prevX.swap(curX);
        prevY.swap(curY);
        previous_low = low;
        previous_high = high;
    }

    AlignResult r;
    r.cells = plan.cells;
    if (best_end.i < 0) return r;

    r.score = best_end.score;
    r.target_end = best_end.i;
    r.candidate_end = best_end.j;

    int i = best_end.i;
    int j = best_end.j;
    int state = best_end.state;
    char prev_gap = '\0';

    auto should_continue = [&]() {
        return mode == Mode::TARGET_GLOBAL ? i > 0 : j > 0;
    };

    while (should_continue()) {
        if (i < 0 || j < 0) return AlignResult{};

        // Free leading overhang boundary: stop when the global sequence
        // has been completely traced back.
        if (mode == Mode::TARGET_GLOBAL && i == 0) break;
        if (mode == Mode::CANDIDATE_GLOBAL && j == 0) break;

        if (j < row_low(i) || j > row_high(i)) {
            throw std::runtime_error("trace left certified band");
        }
        const auto pos = trace_pos(i, j);
        const auto pred = get_pred(trace[pos], state);
        if (pred == 3) return AlignResult{};

        if (state == 0) {
            if (i <= 0 || j <= 0) return AlignResult{};
            if (target[static_cast<std::size_t>(i - 1)] ==
                candidate[static_cast<std::size_t>(j - 1)]) {
                ++r.matches;
            } else {
                ++r.mismatches;
            }
            --i;
            --j;
            prev_gap = '\0';
        } else if (state == 1) {
            if (i <= 0) return AlignResult{};
            ++r.gap_in_candidate;
            if (prev_gap != 'X') ++r.gap_openings;
            prev_gap = 'X';
            --i;
        } else if (state == 2) {
            if (j <= 0) return AlignResult{};
            ++r.gap_in_target;
            if (prev_gap != 'Y') ++r.gap_openings;
            prev_gap = 'Y';
            --j;
        } else {
            return AlignResult{};
        }

        state = static_cast<int>(pred);
    }

    r.target_start = i;
    r.candidate_start = j;
    r.columns = r.matches + r.mismatches +
        r.gap_in_target + r.gap_in_candidate;
    r.valid = (r.matches + r.mismatches) > 0;

    if (r.valid) {
        if (mode == Mode::TARGET_GLOBAL) {
            if (!(r.target_start == 0 && r.target_end == n)) {
                throw std::runtime_error(
                    "TARGET_GLOBAL invariant violated"
                );
            }
        } else {
            if (!(r.candidate_start == 0 && r.candidate_end == m)) {
                throw std::runtime_error(
                    "CANDIDATE_GLOBAL invariant violated"
                );
            }
        }
    }

    return r;
}

std::string fmt10(double x) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.10f", x);
    return std::string(buf);
}

void append_result(
    std::string& line,
    const AlignResult& r,
    std::uint64_t target_len,
    std::uint64_t candidate_len
) {
    auto add = [&](const std::string& s) {
        line.push_back('\t');
        line += s;
    };

    const auto target_span =
        static_cast<std::uint64_t>(r.target_end - r.target_start);
    const auto candidate_span =
        static_cast<std::uint64_t>(r.candidate_end - r.candidate_start);
    const auto aligned_pairs = r.matches + r.mismatches;

    add(r.valid ? "OK" : "FAILED");
    add(std::to_string(r.score));
    add(std::to_string(r.target_start));
    add(std::to_string(r.target_end));
    add(std::to_string(r.candidate_start));
    add(std::to_string(r.candidate_end));
    add(std::to_string(target_span));
    add(std::to_string(candidate_span));
    add(fmt10(
        target_len
            ? static_cast<double>(target_span) /
                static_cast<double>(target_len)
            : 0.0
    ));
    add(fmt10(
        candidate_len
            ? static_cast<double>(candidate_span) /
                static_cast<double>(candidate_len)
            : 0.0
    ));
    add(std::to_string(r.matches));
    add(std::to_string(r.mismatches));
    add(std::to_string(r.gap_in_target));
    add(std::to_string(r.gap_in_candidate));
    add(std::to_string(r.gap_openings));
    add(std::to_string(aligned_pairs));
    add(std::to_string(r.columns));
    add(fmt10(
        aligned_pairs
            ? static_cast<double>(r.matches) /
                static_cast<double>(aligned_pairs)
            : 0.0
    ));
    add(fmt10(
        r.columns
            ? static_cast<double>(r.matches) /
                static_cast<double>(r.columns)
            : 0.0
    ));
    add(std::to_string(r.cells));
}

std::unordered_map<std::string, TargetRec>
load_targets(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open targets: " + path);

    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty targets");
    const auto h = header_index(split_tab(line));
    const auto i_id = require_col(h, "target_id");
    const auto i_acc = require_col(h, "optional_accession");
    const auto i_seq = require_col(h, "normalized_sequence");

    std::unordered_map<std::string, TargetRec> out;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        out.emplace(
            f[i_id],
            TargetRec{f[i_acc], f[i_seq]}
        );
    }
    return out;
}

std::unordered_map<std::uint64_t, CandidateRec>
load_store(const std::string& path, const std::string& expected_release) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open store: " + path);

    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty store");
    const auto h = header_index(split_tab(line));
    const auto i_release = require_col(h, "release");
    const auto i_rid = require_col(h, "rid");
    const auto i_acc = require_col(h, "primary_accession");
    const auto i_seq = require_col(h, "sequence");

    std::unordered_map<std::uint64_t, CandidateRec> out;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f[i_release] != expected_release) {
            throw std::runtime_error("unexpected release in sequence store");
        }
        const auto rid = parse_u64(f[i_rid], "RID");
        out.emplace(rid, CandidateRec{f[i_acc], f[i_seq]});
    }
    return out;
}

std::vector<Job> load_jobs(
    const std::string& path,
    const std::string& start_release,
    const std::string& end_release
) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open jobs: " + path);

    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty jobs");
    const auto h = header_index(split_tab(line));
    const auto i_rel = require_col(h, "release");
    const auto i_tid = require_col(h, "target_id");
    const auto i_rid = require_col(h, "rid");
    const auto i_scope = require_col(h, "job_scope");
    const auto i_chan = require_col(h, "discovery_channel");
    const auto i_exact = require_col(h, "exact_sequence");

    std::vector<Job> out;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f[i_exact] != "0") {
            throw std::runtime_error(
                "06C0 jobs must be NONEXACT"
            );
        }
        if (
            f[i_rel] != start_release &&
            f[i_rel] != end_release
        ) {
            throw std::runtime_error(
                "unexpected release in NONEXACT jobs: " + f[i_rel]
            );
        }
        out.push_back(Job{
            f[i_rel],
            f[i_tid],
            parse_u64(f[i_rid], "RID"),
            f[i_scope],
            f[i_chan]
        });
    }
    return out;
}

} // namespace

int main(int argc, char** argv) {
    try {
        std::string targets_path;
        std::string jobs_path;
        std::string start_store_path;
        std::string end_store_path;
        std::string output_path;
        std::string summary_path;
        std::string start_release_id;
        std::string end_release_id;
        int threads = 1;
        bool plan_only = false;
        std::string plan_output_path;
        std::uint64_t max_cells_per_pair = 0;
        std::uint64_t max_total_cells = 0;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) {
                    throw std::runtime_error("missing value for " + arg);
                }
                return argv[++i];
            };

            if (arg == "--version") {
                std::cout << "probe_directional_glocal_bounded_dp_v2\n";
                return 0;
            }
            if (arg == "--plan-only") plan_only = true;
            else if (arg == "--plan-output") plan_output_path = next();
            else if (arg == "--max-cells-per-pair") {
                max_cells_per_pair = parse_u64(next(), "max-cells-per-pair");
            } else if (arg == "--max-total-cells") {
                max_total_cells = parse_u64(next(), "max-total-cells");
            } else if (arg == "--targets") targets_path = next();
            else if (arg == "--alignment-jobs") jobs_path = next();
            else if (arg == "--start-sequence-store") start_store_path = next();
            else if (arg == "--end-sequence-store") end_store_path = next();
            else if (arg == "--output") output_path = next();
            else if (arg == "--summary") summary_path = next();
            else if (arg == "--start-release-id") start_release_id = next();
            else if (arg == "--end-release-id") end_release_id = next();
            else if (arg == "--threads") {
                threads = static_cast<int>(parse_u64(next(), "threads"));
            } else {
                throw std::runtime_error("unknown argument: " + arg);
            }
        }

        if (
            targets_path.empty() ||
            jobs_path.empty() ||
            start_store_path.empty() ||
            end_store_path.empty() ||
            (!plan_only && output_path.empty()) ||
            (!plan_only && summary_path.empty()) ||
            (plan_only && plan_output_path.empty()) ||
            start_release_id.empty() ||
            end_release_id.empty()
        ) {
            throw std::runtime_error("missing required arguments");
        }

        if (threads < 1) {
            throw std::runtime_error("threads must be positive");
        }
        if (start_release_id == end_release_id) {
            throw std::runtime_error(
                "START and END release IDs must be distinct"
            );
        }

        const auto targets = load_targets(targets_path);
        const auto start_store =
            load_store(start_store_path, start_release_id);
        const auto end_store =
            load_store(end_store_path, end_release_id);
        const auto jobs = load_jobs(
            jobs_path,
            start_release_id,
            end_release_id
        );

        std::vector<std::pair<BandPlan, BandPlan>> plans;
        plans.reserve(jobs.size());
        std::uint64_t planned_total = 0;
        std::string budget_error;
        for (const auto& job : jobs) {
            const auto tit = targets.find(job.target_id);
            if (tit == targets.end()) {
                throw std::runtime_error("target missing: " + job.target_id);
            }
            const auto& store = job.release == start_release_id
                ? start_store : end_store;
            const auto cit = store.find(job.rid);
            if (cit == store.end()) {
                throw std::runtime_error("candidate RID missing from store");
            }
            const auto tg = plan_glocal(
                tit->second.seq, cit->second.seq, Mode::TARGET_GLOBAL
            );
            const auto cg = plan_glocal(
                tit->second.seq, cit->second.seq, Mode::CANDIDATE_GLOBAL
            );
            const auto pair_cells = tg.cells + cg.cells;
            if (planned_total > std::numeric_limits<std::uint64_t>::max() -
                    pair_cells) {
                throw std::runtime_error("DP cell total overflow");
            }
            planned_total += pair_cells;
            if (max_cells_per_pair != 0 && pair_cells > max_cells_per_pair &&
                    budget_error.empty()) {
                budget_error = "INCOMPLETE_BUDGET: certified pair cell budget "
                    "exceeded for " + job.release + ":" + job.target_id +
                    ":" + std::to_string(job.rid) + " required=" +
                    std::to_string(pair_cells) + " limit=" +
                    std::to_string(max_cells_per_pair);
            }
            plans.emplace_back(tg, cg);
        }
        if (!plan_output_path.empty()) {
            const std::string temporary = plan_output_path + ".tmp";
            std::ofstream plan_out(temporary);
            if (!plan_out) throw std::runtime_error("cannot create DP plan");
            plan_out << "release\ttarget_id\trid\tdirection"
                "\ttarget_length\tcandidate_length\tlower_bound_score"
                "\tdiagonal_min\tdiagonal_max\tcertified_cells"
                "\tfull_matrix_cells\n";
            for (std::size_t index = 0; index < jobs.size(); ++index) {
                const auto& job = jobs[index];
                const auto& store = job.release == start_release_id
                    ? start_store : end_store;
                const auto& target = targets.at(job.target_id).seq;
                const auto& candidate = store.at(job.rid).seq;
                for (int direction = 0; direction < 2; ++direction) {
                    const auto& plan = direction == 0
                        ? plans[index].first : plans[index].second;
                    plan_out << job.release << '\t' << job.target_id << '\t'
                        << job.rid << '\t'
                        << (direction == 0 ? "TARGET_GLOBAL" : "CANDIDATE_GLOBAL")
                        << '\t' << target.size() << '\t' << candidate.size()
                        << '\t' << plan.lower_bound << '\t' << plan.diagonal_min
                        << '\t' << plan.diagonal_max << '\t' << plan.cells
                        << '\t' << plan.full_cells << '\n';
                }
            }
            plan_out.close();
            if (!plan_out || std::rename(
                    temporary.c_str(), plan_output_path.c_str()) != 0) {
                throw std::runtime_error("failed to install DP plan");
            }
        }
        // A plan is a complete inventory, including jobs exceeding a budget.
        // It does not publish alignments or classify any pair as completed.
        if (plan_only) return 0;
        if (!budget_error.empty()) throw std::runtime_error(budget_error);
        if (max_total_cells != 0 && planned_total > max_total_cells) {
            throw std::runtime_error(
                "INCOMPLETE_BUDGET: certified total cell budget exceeded "
                "required=" + std::to_string(planned_total) + " limit=" +
                std::to_string(max_total_cells)
            );
        }

#ifdef _OPENMP
        omp_set_num_threads(threads);
#endif

        std::vector<std::string> lines(jobs.size());
        std::vector<std::uint64_t> cells(jobs.size(), 0);
        std::vector<std::string> errors(jobs.size());

#pragma omp parallel for schedule(dynamic, 1) if(threads > 1)
        for (std::int64_t idx = 0;
             idx < static_cast<std::int64_t>(jobs.size());
             ++idx) {
            try {
                const auto& job = jobs[static_cast<std::size_t>(idx)];
                const auto tit = targets.find(job.target_id);
                if (tit == targets.end()) {
                    throw std::runtime_error("target missing: " + job.target_id);
                }

                const auto& store =
                    job.release == start_release_id
                        ? start_store
                        : end_store;
                const auto cit = store.find(job.rid);
                if (cit == store.end()) {
                    throw std::runtime_error("candidate RID missing from store");
                }

                const auto& t = tit->second;
                const auto& c = cit->second;

                const auto tg =
                    align_glocal_bounded(
                        t.seq, c.seq, Mode::TARGET_GLOBAL,
                        plans[static_cast<std::size_t>(idx)].first
                    );
                const auto cg =
                    align_glocal_bounded(
                        t.seq, c.seq, Mode::CANDIDATE_GLOBAL,
                        plans[static_cast<std::size_t>(idx)].second
                    );

                if (!tg.valid || !cg.valid) {
                    throw std::runtime_error(
                        "certified directional glocal DP failed"
                    );
                }

                std::string line;
                line.reserve(1024);
                line += job.release;
                line.push_back('\t');
                line += job.target_id;
                line.push_back('\t');
                line += std::to_string(job.rid);
                line.push_back('\t');
                line += job.job_scope;
                line.push_back('\t');
                line += job.discovery_channel;
                line.push_back('\t');
                line += t.accession;
                line.push_back('\t');
                line += c.accession;
                line.push_back('\t');
                line += std::to_string(t.seq.size());
                line.push_back('\t');
                line += std::to_string(c.seq.size());

                append_result(
                    line, tg, t.seq.size(), c.seq.size()
                );
                append_result(
                    line, cg, t.seq.size(), c.seq.size()
                );

                lines[static_cast<std::size_t>(idx)] = std::move(line);
                cells[static_cast<std::size_t>(idx)] =
                    tg.cells + cg.cells;
            } catch (const std::exception& error) {
                errors[static_cast<std::size_t>(idx)] = error.what();
            }
        }
        for (const auto& error : errors) {
            if (!error.empty()) throw std::runtime_error(error);
        }

        const std::string out_tmp = output_path + ".tmp";
        const std::string sum_tmp = summary_path + ".tmp";

        std::ofstream out(out_tmp);
        if (!out) throw std::runtime_error("cannot create output");

        out
            << "release\ttarget_id\trid\tjob_scope\tdiscovery_channel"
            << "\ttarget_optional_accession\tcandidate_primary_accession"
            << "\ttarget_length\tcandidate_length";

        auto hdr = [&](const std::string& p) {
            out
                << '\t' << p << "_status"
                << '\t' << p << "_score"
                << '\t' << p << "_target_start0"
                << '\t' << p << "_target_end0_exclusive"
                << '\t' << p << "_candidate_start0"
                << '\t' << p << "_candidate_end0_exclusive"
                << '\t' << p << "_target_aligned_span"
                << '\t' << p << "_candidate_aligned_span"
                << '\t' << p << "_target_coverage"
                << '\t' << p << "_candidate_coverage"
                << '\t' << p << "_matches"
                << '\t' << p << "_mismatches"
                << '\t' << p << "_gap_in_target_residues"
                << '\t' << p << "_gap_in_candidate_residues"
                << '\t' << p << "_gap_openings"
                << '\t' << p << "_aligned_residue_pairs"
                << '\t' << p << "_alignment_columns"
                << '\t' << p << "_identity_aligned_pairs"
                << '\t' << p << "_identity_alignment_columns"
                << '\t' << p << "_cells_evaluated";
        };

        hdr("target_global");
        hdr("candidate_global");
        out << '\n';

        for (const auto& line : lines) out << line << '\n';
        out.close();

        std::uint64_t total_cells = 0;
        for (const auto x : cells) total_cells += x;
        if (total_cells != planned_total) {
            throw std::runtime_error("evaluated DP cells differ from plan");
        }

        std::ofstream sum(sum_tmp);
        if (!sum) throw std::runtime_error("cannot create summary");
        sum << "metric\tvalue\n";
        sum << "job_count\t" << jobs.size() << '\n';
        sum << "completed_count\t" << jobs.size() << '\n';
        sum << "threads\t" << threads << '\n';
        sum << "directional_alignment_count\t"
            << (jobs.size() * 2ULL) << '\n';
        sum << "total_dp_cells_evaluated\t"
            << total_cells << '\n';
        sum << "status_OK\t"
            << (jobs.size() * 2ULL) << '\n';
        sum.close();

        if (std::rename(out_tmp.c_str(), output_path.c_str()) != 0) {
            throw std::runtime_error("failed to install output");
        }
        if (std::rename(sum_tmp.c_str(), summary_path.c_str()) != 0) {
            throw std::runtime_error("failed to install summary");
        }

        return 0;

    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
