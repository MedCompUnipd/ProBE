// ProBE candidate-pipeline-v2 (Stage 2C exploratory; not wired to DAG)
// Stage 03 V2: species-wide primary candidate discovery + seed geometry.
// Consume Stage 02B pre-resolved target/OX taxonomic buckets.
// Species-wide discovery is independent of downstream donor policy.
// Input FASTA is the Stage 01C GO-positive enriched UniProt V2 FASTA.
// FRAG=1 records are excluded from the primary whole-protein sequence channel
// but remain available in the full V2 release for later lineage/audit stages.
//
// V2 default: targets at ANY rank search all verified same-species OX.
// Matching across strains is discovery only, NOT GO donation.
// Legacy rank_aware/strict_taxon modes retained for regression comparison.
//
// Candidate-generation rule (experimental v0):
//   shared distinct 7-mers >= min(10, target distinct 7-mers)
//   AND shared / target distinct 7-mers >= 0.25
//
// Geometry is descriptive only. It does not admit/reject biological entities.

#include <algorithm>
#include <cstdint>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

static std::size_t K = 7;
static double MIN_TARGET_KMER_FRACTION = 0.25;
static std::size_t MAX_REQUIRED_SHARED = 10;
static std::string FRAGMENT_POLICY = "exclude";
static std::string TAXONOMY_POLICY = "species_wide";

struct Target {
    std::string id;
    std::string sequence;
    std::string resolved_taxid;
    std::string species_taxid;
    std::string bucket;
    std::uint32_t distinct_kmers = 0;
    std::vector<std::pair<std::uint64_t, std::uint32_t>> unique_positional_kmers;
};

struct OxTaxonomy {
    std::string resolved_taxid;
    std::string species_taxid;
    std::string species_bucket;
    std::string resolved_bucket;
};

struct Occ {
    std::uint32_t first_pos = 0;
    std::uint8_t count = 0;  // 0, 1, or 2 (=2+)
};

struct Geometry {
    std::uint32_t shared_unique_positional = 0;
    double target_seed_span_fraction = 0.0;
    double candidate_seed_span_fraction = 0.0;
    double target_seed_residue_coverage = 0.0;
    double candidate_seed_residue_coverage = 0.0;
    std::uint32_t lis_count = 0;
    double lis_fraction = 0.0;
    std::int64_t diagonal_min = 0;
    std::int64_t diagonal_max = 0;
    std::uint64_t diagonal_range = 0;
};

struct Summary {
    std::uint64_t fasta_records_total = 0;
    std::uint64_t records_missing_ox = 0;
    std::uint64_t records_malformed_ox = 0;
    std::uint64_t records_ox_not_in_taxonomy_map = 0;
    std::uint64_t records_no_primary_bucket = 0;
    std::uint64_t records_fragment_skipped = 0;
    std::uint64_t records_primary_eligible = 0;
    std::uint64_t records_primary_len_lt_k = 0;
    std::uint64_t records_with_emitted_pair = 0;
    std::uint64_t pairs_emitted = 0;
    std::uint64_t exact_sequence_pairs = 0;

    std::uint64_t positional_zero = 0;
    std::uint64_t positional_ge1 = 0;
    std::uint64_t positional_ge5 = 0;
    std::uint64_t positional_ge10 = 0;

    std::uint64_t bidir_span_ge25 = 0;
    std::uint64_t bidir_span_ge50 = 0;
    std::uint64_t bidir_span_ge75 = 0;
    std::uint64_t bidir_span_ge90 = 0;
    std::uint64_t bidir_span_ge95 = 0;

    std::uint64_t bidir_seed_cov_ge25 = 0;
    std::uint64_t bidir_seed_cov_ge50 = 0;
    std::uint64_t bidir_seed_cov_ge75 = 0;
    std::uint64_t bidir_seed_cov_ge90 = 0;

    std::uint64_t lis_ge50 = 0;
    std::uint64_t lis_ge75 = 0;
    std::uint64_t lis_ge90 = 0;
    std::uint64_t lis_ge95 = 0;

    std::uint64_t length_ratio_ge50 = 0;
    std::uint64_t length_ratio_ge75 = 0;
    std::uint64_t length_ratio_ge90 = 0;
    std::uint64_t length_ratio_ge95 = 0;

    std::uint64_t max_pairs_for_one_record = 0;
};

static std::vector<std::string> split_tab(const std::string& line) {
    std::vector<std::string> out;
    std::size_t start = 0;
    while (true) {
        const auto pos = line.find('\t', start);
        if (pos == std::string::npos) {
            out.emplace_back(line.substr(start));
            return out;
        }
        out.emplace_back(line.substr(start, pos - start));
        start = pos + 1;
    }
}

static std::unordered_map<std::string, std::size_t>
header_index(const std::vector<std::string>& fields) {
    std::unordered_map<std::string, std::size_t> out;
    for (std::size_t i = 0; i < fields.size(); ++i) out.emplace(fields[i], i);
    return out;
}

static std::size_t require_column(
    const std::unordered_map<std::string, std::size_t>& h,
    const std::string& name
) {
    const auto it = h.find(name);
    if (it == h.end()) throw std::runtime_error("missing TSV column: " + name);
    return it->second;
}

static std::string choose_column(
    const std::unordered_map<std::string, std::size_t>& h,
    const std::vector<std::string>& names
) {
    for (const auto& name : names) {
        if (h.count(name)) return name;
    }
    std::string msg = "missing TSV column; expected one of:";
    for (const auto& name : names) msg += " " + name;
    throw std::runtime_error(msg);
}

static std::uint64_t pack_kmer(const std::string& s, std::size_t pos) {
    std::uint64_t key = 0;
    for (std::size_t j = 0; j < K; ++j) {
        key = (key << 8) | static_cast<unsigned char>(s[pos + j]);
    }
    return key;
}

static std::string parse_accession(const std::string& header) {
    // UniProt FASTA: sp|ACC|ENTRY ... or tr|ACC|ENTRY ...
    const auto first = header.find('|');
    if (first != std::string::npos) {
        const auto second = header.find('|', first + 1);
        if (second != std::string::npos && second > first + 1) {
            return header.substr(first + 1, second - first - 1);
        }
    }
    const auto end = header.find_first_of(" \t");
    return header.substr(0, end);
}

static std::string parse_entry(const std::string& header) {
    const auto first = header.find('|');
    if (first == std::string::npos) return "";
    const auto second = header.find('|', first + 1);
    if (second == std::string::npos) return "";
    const auto end = header.find_first_of(" \t", second + 1);
    return header.substr(
        second + 1,
        (end == std::string::npos ? header.size() : end) - (second + 1)
    );
}

static std::string token_value(
    const std::string& header,
    const std::string& key
) {
    const std::string needle = " " + key + "=";
    const auto p = header.find(needle);
    if (p == std::string::npos) return "";
    const auto start = p + needle.size();
    const auto end = header.find_first_of(" \t", start);
    return header.substr(
        start,
        (end == std::string::npos ? header.size() : end) - start
    );
}

static bool parse_ox(const std::string& header, std::string& ox, bool& malformed) {
    malformed = false;
    const auto p = header.find(" OX=");
    if (p == std::string::npos) return false;
    const auto start = p + 4;
    std::size_t end = start;
    while (end < header.size() && header[end] >= '0' && header[end] <= '9') ++end;
    if (end == start) {
        malformed = true;
        return false;
    }
    ox = header.substr(start, end - start);
    return true;
}

static std::unordered_map<std::string, std::string>
load_target_sequences(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open targets: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty targets TSV");
    const auto h = header_index(split_tab(line));
    const auto i_id = require_column(h, "target_id");
    const auto i_seq = require_column(h, "normalized_sequence");

    std::unordered_map<std::string, std::string> out;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= std::max(i_id, i_seq)) {
            throw std::runtime_error("malformed targets TSV row");
        }
        out.emplace(f[i_id], f[i_seq]);
    }
    return out;
}

static std::vector<Target> load_targets(
    const std::string& target_path,
    const std::string& target_taxonomy_path
) {
    const auto sequences = load_target_sequences(target_path);

    std::ifstream in(target_taxonomy_path);
    if (!in) throw std::runtime_error("cannot open target taxonomy TSV: " + target_taxonomy_path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty target taxonomy TSV");
    const auto h = header_index(split_tab(line));
    const auto i_id = require_column(h, "target_id");
    const auto i_res = require_column(h, "resolved_taxid");
    const auto i_sp = require_column(h, "species_taxid");
    const auto i_bucket = require_column(h, "primary_bucket");

    std::vector<Target> targets;
    targets.reserve(sequences.size());

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= std::max({i_id, i_res, i_sp, i_bucket})) {
            throw std::runtime_error("malformed target taxonomy row");
        }
        const auto sit = sequences.find(f[i_id]);
        if (sit == sequences.end()) continue;
        if (f[i_res].empty() || f[i_sp].empty()) continue;

        Target t;
        t.id = f[i_id];
        t.sequence = sit->second;
        t.resolved_taxid = f[i_res];
        t.species_taxid = f[i_sp];

        if (TAXONOMY_POLICY == "species_wide") {
            t.bucket = "S:" + t.species_taxid;
        } else if (TAXONOMY_POLICY == "strict_taxon") {
            t.bucket = "T:" + t.resolved_taxid;
        } else {
            t.bucket = f[i_bucket];
        }

        if (t.bucket.empty()) continue;

        if (t.sequence.size() >= K) {
            std::unordered_map<std::uint64_t, Occ> occ;
            occ.reserve((t.sequence.size() - K + 1) * 2 + 1);
            for (std::size_t p = 0; p + K <= t.sequence.size(); ++p) {
                const auto key = pack_kmer(t.sequence, p);
                auto& o = occ[key];
                if (o.count == 0) {
                    o.first_pos = static_cast<std::uint32_t>(p);
                    o.count = 1;
                } else {
                    o.count = 2;
                }
            }
            t.distinct_kmers = static_cast<std::uint32_t>(occ.size());
            t.unique_positional_kmers.reserve(occ.size());
            for (const auto& kv : occ) {
                if (kv.second.count == 1) {
                    t.unique_positional_kmers.emplace_back(kv.first, kv.second.first_pos);
                }
            }
            std::sort(
                t.unique_positional_kmers.begin(),
                t.unique_positional_kmers.end(),
                [](const auto& a, const auto& b) { return a.second < b.second; }
            );
        }
        targets.push_back(std::move(t));
    }

    if (targets.empty()) throw std::runtime_error("no targets loaded");
    return targets;
}

static std::unordered_map<std::string, OxTaxonomy>
load_ox_taxonomy(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open OX taxonomy TSV: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty OX taxonomy TSV");
    const auto h = header_index(split_tab(line));

    const auto ox_name = choose_column(h, {"raw_ox", "raw_taxid", "ox"});
    const auto i_ox = require_column(h, ox_name);
    const auto i_res = require_column(h, "resolved_taxid");
    const auto i_sp = require_column(h, "species_taxid");
    const auto i_sb = require_column(h, "species_bucket");
    const auto i_tb = require_column(h, "resolved_bucket");

    std::unordered_map<std::string, OxTaxonomy> out;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= std::max({i_ox, i_res, i_sp, i_sb, i_tb})) {
            throw std::runtime_error("malformed OX taxonomy row");
        }
        out.emplace(
            f[i_ox],
            OxTaxonomy{f[i_res], f[i_sp], f[i_sb], f[i_tb]}
        );
    }
    return out;
}

using PostingMap = std::unordered_map<std::uint64_t, std::vector<std::uint32_t>>;

static std::unordered_map<std::string, PostingMap>
build_index(const std::vector<Target>& targets) {
    std::unordered_map<std::string, PostingMap> index;

    for (std::uint32_t tid = 0; tid < targets.size(); ++tid) {
        const auto& t = targets[tid];
        if (t.distinct_kmers == 0) continue;

        std::unordered_set<std::uint64_t> distinct;
        distinct.reserve(t.distinct_kmers * 2 + 1);
        for (std::size_t p = 0; p + K <= t.sequence.size(); ++p) {
            distinct.insert(pack_kmer(t.sequence, p));
        }

        auto& bucket = index[t.bucket];
        for (const auto key : distinct) bucket[key].push_back(tid);
    }
    return index;
}

static std::unordered_map<std::uint64_t, Occ>
candidate_occurrences(const std::string& seq) {
    std::unordered_map<std::uint64_t, Occ> occ;
    if (seq.size() < K) return occ;
    occ.reserve((seq.size() - K + 1) * 2 + 1);
    for (std::size_t p = 0; p + K <= seq.size(); ++p) {
        const auto key = pack_kmer(seq, p);
        auto& o = occ[key];
        if (o.count == 0) {
            o.first_pos = static_cast<std::uint32_t>(p);
            o.count = 1;
        } else {
            o.count = 2;
        }
    }
    return occ;
}

static std::uint32_t lis_length(const std::vector<std::uint32_t>& values) {
    std::vector<std::uint32_t> tails;
    tails.reserve(values.size());
    for (const auto v : values) {
        const auto it = std::lower_bound(tails.begin(), tails.end(), v);
        if (it == tails.end()) tails.push_back(v);
        else *it = v;
    }
    return static_cast<std::uint32_t>(tails.size());
}

static double interval_coverage_fraction(
    std::vector<std::uint32_t> starts,
    std::size_t sequence_length
) {
    if (starts.empty() || sequence_length == 0) return 0.0;
    std::sort(starts.begin(), starts.end());

    std::uint64_t covered = 0;
    std::uint64_t left = starts[0];
    std::uint64_t right = left + K;

    for (std::size_t i = 1; i < starts.size(); ++i) {
        const std::uint64_t l = starts[i];
        const std::uint64_t r = l + K;
        if (l <= right) {
            right = std::max(right, r);
        } else {
            covered += right - left;
            left = l;
            right = r;
        }
    }
    covered += right - left;
    if (covered > sequence_length) covered = sequence_length;
    return static_cast<double>(covered) / static_cast<double>(sequence_length);
}

static Geometry compute_geometry(
    const Target& target,
    const std::string& candidate,
    const std::unordered_map<std::uint64_t, Occ>& cand_occ
) {
    Geometry g;
    std::vector<std::uint32_t> qpos;
    std::vector<std::uint32_t> spos;
    qpos.reserve(target.unique_positional_kmers.size());
    spos.reserve(target.unique_positional_kmers.size());

    std::int64_t dmin = std::numeric_limits<std::int64_t>::max();
    std::int64_t dmax = std::numeric_limits<std::int64_t>::min();

    for (const auto& seed : target.unique_positional_kmers) {
        const auto it = cand_occ.find(seed.first);
        if (it == cand_occ.end() || it->second.count != 1) continue;

        const auto q = seed.second;
        const auto s = it->second.first_pos;
        qpos.push_back(q);
        spos.push_back(s);

        const auto d = static_cast<std::int64_t>(s) - static_cast<std::int64_t>(q);
        dmin = std::min(dmin, d);
        dmax = std::max(dmax, d);
    }

    g.shared_unique_positional = static_cast<std::uint32_t>(qpos.size());
    if (qpos.empty()) return g;

    const auto [qmin_it, qmax_it] = std::minmax_element(qpos.begin(), qpos.end());
    const auto [smin_it, smax_it] = std::minmax_element(spos.begin(), spos.end());

    const std::uint64_t qspan = static_cast<std::uint64_t>(*qmax_it) - *qmin_it + K;
    const std::uint64_t sspan = static_cast<std::uint64_t>(*smax_it) - *smin_it + K;

    g.target_seed_span_fraction =
        static_cast<double>(std::min<std::uint64_t>(qspan, target.sequence.size())) /
        static_cast<double>(target.sequence.size());
    g.candidate_seed_span_fraction =
        static_cast<double>(std::min<std::uint64_t>(sspan, candidate.size())) /
        static_cast<double>(candidate.size());

    g.target_seed_residue_coverage =
        interval_coverage_fraction(qpos, target.sequence.size());
    g.candidate_seed_residue_coverage =
        interval_coverage_fraction(spos, candidate.size());

    g.lis_count = lis_length(spos);
    g.lis_fraction =
        static_cast<double>(g.lis_count) / static_cast<double>(qpos.size());

    g.diagonal_min = dmin;
    g.diagonal_max = dmax;
    g.diagonal_range = static_cast<std::uint64_t>(dmax - dmin);
    return g;
}

static void update_summary(
    Summary& s,
    const Geometry& g,
    double length_ratio,
    bool exact
) {
    ++s.pairs_emitted;
    if (exact) ++s.exact_sequence_pairs;

    if (g.shared_unique_positional == 0) ++s.positional_zero;
    if (g.shared_unique_positional >= 1) ++s.positional_ge1;
    if (g.shared_unique_positional >= 5) ++s.positional_ge5;
    if (g.shared_unique_positional >= 10) ++s.positional_ge10;

    const double bsp = std::min(
        g.target_seed_span_fraction, g.candidate_seed_span_fraction
    );
    if (bsp >= .25) ++s.bidir_span_ge25;
    if (bsp >= .50) ++s.bidir_span_ge50;
    if (bsp >= .75) ++s.bidir_span_ge75;
    if (bsp >= .90) ++s.bidir_span_ge90;
    if (bsp >= .95) ++s.bidir_span_ge95;

    const double bcov = std::min(
        g.target_seed_residue_coverage, g.candidate_seed_residue_coverage
    );
    if (bcov >= .25) ++s.bidir_seed_cov_ge25;
    if (bcov >= .50) ++s.bidir_seed_cov_ge50;
    if (bcov >= .75) ++s.bidir_seed_cov_ge75;
    if (bcov >= .90) ++s.bidir_seed_cov_ge90;

    if (g.lis_fraction >= .50) ++s.lis_ge50;
    if (g.lis_fraction >= .75) ++s.lis_ge75;
    if (g.lis_fraction >= .90) ++s.lis_ge90;
    if (g.lis_fraction >= .95) ++s.lis_ge95;

    if (length_ratio >= .50) ++s.length_ratio_ge50;
    if (length_ratio >= .75) ++s.length_ratio_ge75;
    if (length_ratio >= .90) ++s.length_ratio_ge90;
    if (length_ratio >= .95) ++s.length_ratio_ge95;
}

static void write_summary(
    const std::string& path,
    const std::string& snapshot,
    const std::vector<Target>& targets,
    const Summary& s
) {
    std::ofstream out(path);
    if (!out) throw std::runtime_error("cannot create summary: " + path);

    std::uint64_t unsearchable = 0;
    for (const auto& t : targets) if (t.distinct_kmers == 0) ++unsearchable;

    auto row = [&](const std::string& k, const auto& v) {
        out << k << '\t' << v << '\n';
    };

    out << "metric\tvalue\n";
    row("snapshot", snapshot);
    row("k", K);
    row("max_required_shared", MAX_REQUIRED_SHARED);
    row("min_target_kmer_fraction", MIN_TARGET_KMER_FRACTION);
    row("fragment_policy", FRAGMENT_POLICY);
    row("taxonomy_policy", TAXONOMY_POLICY);
    row("targets_loaded", targets.size());
    row("targets_unsearchable_len_lt_k", unsearchable);
    row("fasta_records_total", s.fasta_records_total);
    row("records_missing_ox", s.records_missing_ox);
    row("records_malformed_ox", s.records_malformed_ox);
    row("records_ox_not_in_taxonomy_map", s.records_ox_not_in_taxonomy_map);
    row("records_no_primary_bucket", s.records_no_primary_bucket);
    row("records_fragment_skipped", s.records_fragment_skipped);
    row("records_primary_eligible", s.records_primary_eligible);
    row("records_primary_len_lt_k", s.records_primary_len_lt_k);
    row("records_with_emitted_pair", s.records_with_emitted_pair);
    row("pairs_emitted", s.pairs_emitted);
    row("exact_sequence_pairs", s.exact_sequence_pairs);
    row("pairs_positional_seed_zero", s.positional_zero);
    row("pairs_positional_seed_ge1", s.positional_ge1);
    row("pairs_positional_seed_ge5", s.positional_ge5);
    row("pairs_positional_seed_ge10", s.positional_ge10);
    row("pairs_bidir_span_ge_0.25", s.bidir_span_ge25);
    row("pairs_bidir_span_ge_0.50", s.bidir_span_ge50);
    row("pairs_bidir_span_ge_0.75", s.bidir_span_ge75);
    row("pairs_bidir_span_ge_0.90", s.bidir_span_ge90);
    row("pairs_bidir_span_ge_0.95", s.bidir_span_ge95);
    row("pairs_bidir_seed_cov_ge_0.25", s.bidir_seed_cov_ge25);
    row("pairs_bidir_seed_cov_ge_0.50", s.bidir_seed_cov_ge50);
    row("pairs_bidir_seed_cov_ge_0.75", s.bidir_seed_cov_ge75);
    row("pairs_bidir_seed_cov_ge_0.90", s.bidir_seed_cov_ge90);
    row("pairs_lis_fraction_ge_0.50", s.lis_ge50);
    row("pairs_lis_fraction_ge_0.75", s.lis_ge75);
    row("pairs_lis_fraction_ge_0.90", s.lis_ge90);
    row("pairs_lis_fraction_ge_0.95", s.lis_ge95);
    row("pairs_length_ratio_ge_0.50", s.length_ratio_ge50);
    row("pairs_length_ratio_ge_0.75", s.length_ratio_ge75);
    row("pairs_length_ratio_ge_0.90", s.length_ratio_ge90);
    row("pairs_length_ratio_ge_0.95", s.length_ratio_ge95);
    row("max_pairs_for_one_record", s.max_pairs_for_one_record);
}

static std::size_t parse_size_arg(
    const std::string& raw,
    const std::string& option
) {
    std::size_t pos = 0;
    unsigned long long parsed = 0;

    try {
        parsed = std::stoull(raw, &pos);
    } catch (const std::exception&) {
        throw std::runtime_error(
            option + " requires an integer value: " + raw
        );
    }

    if (pos != raw.size()) {
        throw std::runtime_error(
            option + " requires an integer value: " + raw
        );
    }

    if (
        parsed >
        static_cast<unsigned long long>(
            std::numeric_limits<std::size_t>::max()
        )
    ) {
        throw std::runtime_error(
            option + " value is too large: " + raw
        );
    }

    return static_cast<std::size_t>(parsed);
}

static double parse_double_arg(
    const std::string& raw,
    const std::string& option
) {
    std::size_t pos = 0;
    double parsed = 0.0;

    try {
        parsed = std::stod(raw, &pos);
    } catch (const std::exception&) {
        throw std::runtime_error(
            option + " requires a floating-point value: " + raw
        );
    }

    if (pos != raw.size() || !std::isfinite(parsed)) {
        throw std::runtime_error(
            option + " requires a finite floating-point value: " + raw
        );
    }

    return parsed;
}

static void print_help(std::ostream& out) {
    out
        << "ProBE primary sequence candidate scanner v2\n\n"
        << "Required inputs:\n"
        << "  --targets PATH\n"
        << "  --target-taxonomy PATH\n"
        << "  --ox-taxonomy PATH\n"
        << "  --fasta PATH\n"
        << "  --snapshot ID\n"
        << "  --out-prefix PATH\n\n"
        << "Scientific parameters:\n"
        << "  --k INTEGER\n"
        << "      Exact amino-acid seed length.\n"
        << "      Allowed range: 4..8. Default: 7.\n"
        << "  --max-required-shared INTEGER\n"
        << "      Maximum required number of shared distinct k-mers.\n"
        << "      Allowed range: 1..65535. Default: 10.\n"
        << "  --min-target-kmer-fraction FLOAT\n"
        << "      Minimum fraction of the target's distinct k-mers shared\n"
        << "      by a candidate. Allowed range: 0.20..1.00.\n"
        << "      Default: 0.25. This is NOT sequence percentage identity.\n"
        << "  --fragment-policy {exclude,include}\n"
        << "      exclude: FRAG=1 records are excluded from the primary\n"
        << "      sequence candidate channel.\n"
        << "      include: FRAG=0 and FRAG=1 records may enter.\n"
        << "      Default: exclude.\n"
        << "  --taxonomy-policy {species_wide,rank_aware,strict_taxon}\n"
        << "      species_wide: all targets search every resolved TaxID\n"
        << "      mapped to the same species, regardless of target rank.\n"
        << "      This changes candidate discovery, NOT GO donor rules.\n"
        << "      rank_aware: species targets accept the same species\n"
        << "      anchor including subtaxa; targets below species require\n"
        << "      the same resolved TaxID.\n"
        << "      strict_taxon: candidate and target must have exactly the\n"
        << "      same resolved TaxID regardless of target rank.\n"
        << "      Default: species_wide in v2.\n\n"
        << "Candidate admission requires BOTH conditions:\n"
        << "  shared distinct k-mers >= min(max_required_shared,\n"
        << "      target distinct k-mers)\n"
        << "  shared distinct k-mers / target distinct k-mers >=\n"
        << "      min_target_kmer_fraction\n";
}

int main(int argc, char** argv) {
    try {
        std::string targets_path;
        std::string target_tax_path;
        std::string ox_tax_path;
        std::string fasta_path;
        std::string snapshot;
        std::string out_prefix;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];

            if (arg == "--help" || arg == "-h") {
                print_help(std::cout);
                return 0;
            }

            auto value = [&]() -> std::string {
                if (i + 1 >= argc) {
                    throw std::runtime_error(
                        "missing value for " + arg
                    );
                }
                return argv[++i];
            };

            if (arg == "--targets") {
                targets_path = value();
            } else if (arg == "--target-taxonomy") {
                target_tax_path = value();
            } else if (arg == "--ox-taxonomy") {
                ox_tax_path = value();
            } else if (arg == "--fasta") {
                fasta_path = value();
            } else if (arg == "--snapshot") {
                snapshot = value();
            } else if (arg == "--out-prefix") {
                out_prefix = value();
            } else if (arg == "--k") {
                K = parse_size_arg(value(), arg);
            } else if (arg == "--max-required-shared") {
                MAX_REQUIRED_SHARED = parse_size_arg(value(), arg);
            } else if (arg == "--min-target-kmer-fraction") {
                MIN_TARGET_KMER_FRACTION =
                    parse_double_arg(value(), arg);
            } else if (arg == "--fragment-policy") {
                FRAGMENT_POLICY = value();
            } else if (arg == "--taxonomy-policy") {
                TAXONOMY_POLICY = value();
            } else {
                throw std::runtime_error(
                    "unknown argument: " + arg
                );
            }
        }

        if (
            targets_path.empty() ||
            target_tax_path.empty() ||
            ox_tax_path.empty() ||
            fasta_path.empty() ||
            snapshot.empty() ||
            out_prefix.empty()
        ) {
            throw std::runtime_error(
                "required: --targets --target-taxonomy --ox-taxonomy "
                "--fasta --snapshot --out-prefix"
            );
        }

        if (K < 4 || K > 8) {
            throw std::runtime_error(
                "--k must be in the range 4..8"
            );
        }

        if (
            MAX_REQUIRED_SHARED < 1 ||
            MAX_REQUIRED_SHARED > 65535
        ) {
            throw std::runtime_error(
                "--max-required-shared must be in the range 1..65535"
            );
        }

        if (
            MIN_TARGET_KMER_FRACTION < 0.20 ||
            MIN_TARGET_KMER_FRACTION > 1.00
        ) {
            throw std::runtime_error(
                "--min-target-kmer-fraction must be in the range "
                "0.20..1.00"
            );
        }

        if (
            FRAGMENT_POLICY != "exclude" &&
            FRAGMENT_POLICY != "include"
        ) {
            throw std::runtime_error(
                "--fragment-policy must be exclude or include"
            );
        }

        if (
            TAXONOMY_POLICY != "species_wide" &&
            TAXONOMY_POLICY != "rank_aware" &&
            TAXONOMY_POLICY != "strict_taxon"
        ) {
            throw std::runtime_error(
                "--taxonomy-policy must be species_wide, "
                "rank_aware or strict_taxon"
            );
        }

        std::cerr << "loading targets...\n";
        auto targets = load_targets(targets_path, target_tax_path);

        std::cerr << "building bucketed k=" << K
                  << " inverted index...\n";
        auto index = build_index(targets);
        std::cerr << "buckets indexed: " << index.size() << "\n";

        std::cerr << "loading OX taxonomy map...\n";
        const auto ox_map = load_ox_taxonomy(ox_tax_path);

        const std::string pairs_path = out_prefix + ".pairs.tsv";
        const std::string pairs_tmp = pairs_path + ".tmp";
        const std::string unsearchable_path = out_prefix + ".unsearchable_targets.tsv";
        const std::string unsearchable_tmp = unsearchable_path + ".tmp";
        const std::string summary_path = out_prefix + ".summary.tsv";
        const std::string summary_tmp = summary_path + ".tmp";

        std::ofstream pairs(pairs_tmp);
        if (!pairs) throw std::runtime_error("cannot create pair output");
        pairs
            << "record_number\taccession\tentry\trid\tsrc\trec"
            << "\traw_ox\tresolved_taxid\tspecies_taxid"
            << "\tfrag\tpe\tsv\tflags\tsec\tgene"
            << "\ttarget_id\ttarget_bucket\ttarget_length\tcandidate_length"
            << "\tlength_ratio\tcandidate_over_target_length"
            << "\ttarget_distinct_kmers\tshared_distinct_kmers\ttarget_shared_fraction"
            << "\texact_sequence"
            << "\tshared_unique_positional_kmers"
            << "\ttarget_seed_span_fraction\tcandidate_seed_span_fraction"
            << "\ttarget_seed_residue_coverage\tcandidate_seed_residue_coverage"
            << "\tlis_count\tlis_fraction"
            << "\tdiagonal_min\tdiagonal_max\tdiagonal_range\n";

        std::ofstream unsearchable(unsearchable_tmp);
        if (!unsearchable) throw std::runtime_error("cannot create unsearchable output");
        unsearchable << "target_id\tlength\treason\n";
        for (const auto& t : targets) {
            if (t.distinct_kmers == 0) {
                unsearchable << t.id << '\t' << t.sequence.size()
                             << "\tLEN_LT_K\n";
            }
        }

        Summary summary;
        std::vector<std::uint16_t> counts(targets.size(), 0);
        std::vector<std::uint32_t> touched;
        touched.reserve(10000);

        std::ifstream fasta(fasta_path);
        if (!fasta) throw std::runtime_error("cannot open FASTA: " + fasta_path);

        std::string line, header, seq;
        std::uint64_t record_number = 0;

        auto process_record = [&]() {
            if (header.empty()) return;
            ++record_number;
            ++summary.fasta_records_total;
            if (record_number % 10000000ULL == 0) {
                std::cerr << "[03] records=" << record_number
                          << " primary_eligible=" << summary.records_primary_eligible
                          << " fragments_skipped=" << summary.records_fragment_skipped
                          << " pairs_emitted=" << summary.pairs_emitted << "\n";
            }

            std::string raw_ox;
            bool malformed = false;
            if (!parse_ox(header, raw_ox, malformed)) {
                if (malformed) ++summary.records_malformed_ox;
                else ++summary.records_missing_ox;
                return;
            }

            const auto oit = ox_map.find(raw_ox);
            if (oit == ox_map.end()) {
                ++summary.records_ox_not_in_taxonomy_map;
                return;
            }
            const auto& ot = oit->second;
            if (ot.resolved_taxid.empty() || ot.species_taxid.empty()) {
                ++summary.records_no_primary_bucket;
                return;
            }

            if (TAXONOMY_POLICY == "species_wide" &&
                ot.species_bucket != "S:" + ot.species_taxid) {
                throw std::runtime_error(
                    "02B OX species bucket does not match resolved species"
                );
            }
            std::vector<const PostingMap*> eligible;
            eligible.reserve(2);

            if (
                (TAXONOMY_POLICY == "rank_aware" ||
                 TAXONOMY_POLICY == "species_wide") &&
                !ot.species_bucket.empty()
            ) {
                const auto it = index.find(ot.species_bucket);
                if (it != index.end()) {
                    eligible.push_back(&it->second);
                }
            }

            if (TAXONOMY_POLICY != "species_wide" &&
                !ot.resolved_bucket.empty()) {
                const auto it = index.find(ot.resolved_bucket);
                if (it != index.end()) {
                    eligible.push_back(&it->second);
                }
            }

            if (eligible.empty()) {
                ++summary.records_no_primary_bucket;
                return;
            }

            ++summary.records_primary_eligible;

            const std::string frag = token_value(header, "FRAG");
            if (frag.empty()) {
                throw std::runtime_error("enriched V2 header missing FRAG token");
            }
            if (frag != "0" && frag != "1") {
                throw std::runtime_error(
                    "invalid FRAG token: " + frag
                );
            }
            if (
                frag == "1" &&
                FRAGMENT_POLICY == "exclude"
            ) {
                ++summary.records_fragment_skipped;
                return;
            }

            if (seq.size() < K) {
                ++summary.records_primary_len_lt_k;
                return;
            }

            std::unordered_set<std::uint64_t> cand_distinct;
            cand_distinct.reserve((seq.size() - K + 1) * 2 + 1);
            for (std::size_t p = 0; p + K <= seq.size(); ++p) {
                cand_distinct.insert(pack_kmer(seq, p));
            }

            touched.clear();
            for (const auto key : cand_distinct) {
                for (const auto* bucket : eligible) {
                    const auto pit = bucket->find(key);
                    if (pit == bucket->end()) continue;
                    for (const auto tid : pit->second) {
                        if (counts[tid] == 0) touched.push_back(tid);
                        if (counts[tid] != std::numeric_limits<std::uint16_t>::max()) {
                            ++counts[tid];
                        }
                    }
                }
            }

            std::vector<std::uint32_t> passing;
            passing.reserve(16);
            for (const auto tid : touched) {
                const auto& t = targets[tid];
                if (TAXONOMY_POLICY == "species_wide" &&
                    t.species_taxid != ot.species_taxid) continue;
                const std::uint32_t shared = counts[tid];
                const std::uint32_t need = std::min<std::uint32_t>(
                    MAX_REQUIRED_SHARED, t.distinct_kmers
                );
                const double frac = t.distinct_kmers
                    ? static_cast<double>(shared) / t.distinct_kmers
                    : 0.0;

                if (shared >= need && frac >= MIN_TARGET_KMER_FRACTION) {
                    passing.push_back(tid);
                }
            }

            if (!passing.empty()) {
                ++summary.records_with_emitted_pair;
                summary.max_pairs_for_one_record = std::max<std::uint64_t>(
                    summary.max_pairs_for_one_record, passing.size()
                );

                const auto cand_occ = candidate_occurrences(seq);
                const auto accession = parse_accession(header);
                const auto entry = parse_entry(header);
                const auto rid = token_value(header, "RID");
                const auto src = token_value(header, "SRC");
                const auto rec = token_value(header, "REC");
                const auto pe = token_value(header, "PE");
                const auto sv = token_value(header, "SV");
                const auto flags = token_value(header, "FLAGS");
                const auto sec = token_value(header, "SEC");
                const auto gene = token_value(header, "GENE");

                for (const auto tid : passing) {
                    const auto& t = targets[tid];
                    const auto shared = counts[tid];
                    const double target_frac =
                        static_cast<double>(shared) / t.distinct_kmers;
                    const bool exact = (t.sequence == seq);

                    const double len_ratio =
                        static_cast<double>(std::min(t.sequence.size(), seq.size())) /
                        static_cast<double>(std::max(t.sequence.size(), seq.size()));
                    const double cand_over_target =
                        static_cast<double>(seq.size()) /
                        static_cast<double>(t.sequence.size());

                    const auto g = compute_geometry(t, seq, cand_occ);
                    update_summary(summary, g, len_ratio, exact);

                    pairs
                        << record_number << '\t'
                        << accession << '\t'
                        << entry << '\t'
                        << rid << '\t'
                        << src << '\t'
                        << rec << '\t'
                        << raw_ox << '\t'
                        << ot.resolved_taxid << '\t'
                        << ot.species_taxid << '\t'
                        << frag << '\t'
                        << pe << '\t'
                        << sv << '\t'
                        << flags << '\t'
                        << sec << '\t'
                        << gene << '\t'
                        << t.id << '\t'
                        << t.bucket << '\t'
                        << t.sequence.size() << '\t'
                        << seq.size() << '\t'
                        << std::setprecision(10) << len_ratio << '\t'
                        << cand_over_target << '\t'
                        << t.distinct_kmers << '\t'
                        << shared << '\t'
                        << target_frac << '\t'
                        << (exact ? 1 : 0) << '\t'
                        << g.shared_unique_positional << '\t'
                        << g.target_seed_span_fraction << '\t'
                        << g.candidate_seed_span_fraction << '\t'
                        << g.target_seed_residue_coverage << '\t'
                        << g.candidate_seed_residue_coverage << '\t'
                        << g.lis_count << '\t'
                        << g.lis_fraction << '\t'
                        << g.diagonal_min << '\t'
                        << g.diagonal_max << '\t'
                        << g.diagonal_range << '\n';
                }
            }

            for (const auto tid : touched) counts[tid] = 0;
        };

        while (std::getline(fasta, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (!line.empty() && line[0] == '>') {
                process_record();
                header = line.substr(1);
                seq.clear();
            } else if (!header.empty()) {
                seq += line;
            }
        }
        process_record();

        pairs.flush();
        unsearchable.flush();
        if (!pairs || !unsearchable) {
            throw std::runtime_error("write failure while finalizing Stage 03 outputs");
        }
        pairs.close();
        unsearchable.close();

        write_summary(summary_tmp, snapshot, targets, summary);

        if (std::rename(pairs_tmp.c_str(), pairs_path.c_str()) != 0) {
            throw std::runtime_error("failed to install pair output");
        }
        if (std::rename(unsearchable_tmp.c_str(), unsearchable_path.c_str()) != 0) {
            throw std::runtime_error("failed to install unsearchable-target output");
        }
        if (std::rename(summary_tmp.c_str(), summary_path.c_str()) != 0) {
            throw std::runtime_error("failed to install summary output");
        }

        std::cerr << "wrote " << pairs_path << "\n";
        std::cerr << "wrote " << summary_path << "\n";
        std::cerr << "wrote " << unsearchable_path << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
