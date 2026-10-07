// Production ProBE candidate pipeline.
// Stage 06C0: exact full-DP directional glocal alignment of NONEXACT jobs.
//
// Two alignments are computed independently for every NONEXACT pair:
//   TARGET_GLOBAL:
//     consume the complete target; candidate N/C overhangs are free.
//   CANDIDATE_GLOBAL:
//     consume the complete candidate; target N/C overhangs are free.
//
// This program intentionally uses FULL DP only.  It is a semantic reference
// implementation for validating the later optimized/banded directional glocal
// aligner.  It makes NO SAME_ENTITY decision.

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

AlignResult align_glocal_full(
    const std::string& target,
    const std::string& candidate,
    Mode mode
) {
    const int n = static_cast<int>(target.size());
    const int m = static_cast<int>(candidate.size());
    const std::uint64_t width = static_cast<std::uint64_t>(m + 1);
    const std::uint64_t cells =
        static_cast<std::uint64_t>(n + 1) * width;

    std::vector<std::uint8_t> trace(
        static_cast<std::size_t>(cells),
        static_cast<std::uint8_t>(0x3F)
    );

    std::vector<int> prevM(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> prevX(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> prevY(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curM(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curX(static_cast<std::size_t>(m + 1), NEG);
    std::vector<int> curY(static_cast<std::size_t>(m + 1), NEG);

    // Row 0.
    prevM[0] = 0;

    if (mode == Mode::TARGET_GLOBAL) {
        // Candidate leading overhang is free.
        for (int j = 1; j <= m; ++j) {
            prevY[static_cast<std::size_t>(j)] = 0;
        }
    } else {
        // Candidate is global: its leading residues are penalized.
        for (int j = 1; j <= m; ++j) {
            const std::size_t idx =
                static_cast<std::size_t>(j);
            if (j == 1) {
                prevY[idx] = GAP_OPEN;
                set_pred(trace[idx], 2, 0);
            } else {
                prevY[idx] =
                    prevY[static_cast<std::size_t>(j - 1)] + GAP_EXTEND;
                set_pred(trace[idx], 2, 2);
            }
        }
    }

    EndPoint best_end;

    for (int i = 1; i <= n; ++i) {
        std::fill(curM.begin(), curM.end(), NEG);
        std::fill(curX.begin(), curX.end(), NEG);
        std::fill(curY.begin(), curY.end(), NEG);

        const std::uint64_t rowoff =
            static_cast<std::uint64_t>(i) * width;

        if (mode == Mode::CANDIDATE_GLOBAL) {
            // Target leading overhang is free.
            curX[0] = 0;
        } else {
            // Target is global: its leading residues are penalized.
            if (i == 1) {
                curX[0] = GAP_OPEN;
                set_pred(
                    trace[static_cast<std::size_t>(rowoff)],
                    1,
                    0
                );
            } else {
                curX[0] = prevX[0] + GAP_EXTEND;
                set_pred(
                    trace[static_cast<std::size_t>(rowoff)],
                    1,
                    1
                );
            }
        }

        for (int j = 1; j <= m; ++j) {
            const auto idx = static_cast<std::size_t>(j);
            auto& tb = trace[static_cast<std::size_t>(
                rowoff + static_cast<std::uint64_t>(j)
            )];

            const auto mc = best3(
                prevM[static_cast<std::size_t>(j - 1)], 0,
                prevX[static_cast<std::size_t>(j - 1)], 1,
                prevY[static_cast<std::size_t>(j - 1)], 2
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
                prevX[idx] > NEG / 2
                    ? prevX[idx] + GAP_EXTEND
                    : NEG,
                1,
                prevM[idx] > NEG / 2
                    ? prevM[idx] + GAP_OPEN
                    : NEG,
                0,
                prevY[idx] > NEG / 2
                    ? prevY[idx] + GAP_OPEN
                    : NEG,
                2
            );
            curX[idx] = xc.score;
            if (xc.score > NEG / 2) set_pred(tb, 1, xc.pred);

            const auto yc = best3(
                curY[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curY[static_cast<std::size_t>(j - 1)] + GAP_EXTEND
                    : NEG,
                2,
                curM[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curM[static_cast<std::size_t>(j - 1)] + GAP_OPEN
                    : NEG,
                0,
                curX[static_cast<std::size_t>(j - 1)] > NEG / 2
                    ? curX[static_cast<std::size_t>(j - 1)] + GAP_OPEN
                    : NEG,
                1
            );
            curY[idx] = yc.score;
            if (yc.score > NEG / 2) set_pred(tb, 2, yc.pred);
        }

        if (mode == Mode::TARGET_GLOBAL && i == n) {
            // Candidate trailing overhang is free: endpoint anywhere on row n.
            for (int j = 0; j <= m; ++j) {
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
        } else if (mode == Mode::CANDIDATE_GLOBAL) {
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
    }

    AlignResult r;
    r.cells = cells;
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

        const auto pos = static_cast<std::size_t>(
            static_cast<std::uint64_t>(i) * width +
            static_cast<std::uint64_t>(j)
        );
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

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) {
                    throw std::runtime_error("missing value for " + arg);
                }
                return argv[++i];
            };

            if (arg == "--targets") targets_path = next();
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
            output_path.empty() ||
            summary_path.empty() ||
            start_release_id.empty() ||
            end_release_id.empty()
        ) {
            throw std::runtime_error("missing required arguments");
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

#ifdef _OPENMP
        omp_set_num_threads(threads);
#endif

        std::vector<std::string> lines(jobs.size());
        std::vector<std::uint64_t> cells(jobs.size(), 0);

#pragma omp parallel for schedule(dynamic, 1) if(threads > 1)
        for (std::int64_t idx = 0;
             idx < static_cast<std::int64_t>(jobs.size());
             ++idx) {
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
                align_glocal_full(t.seq, c.seq, Mode::TARGET_GLOBAL);
            const auto cg =
                align_glocal_full(t.seq, c.seq, Mode::CANDIDATE_GLOBAL);

            if (!tg.valid || !cg.valid) {
                throw std::runtime_error(
                    "directional glocal full-DP failed"
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
