// Production ProBE candidate pipeline.
// Stage 04D2: scan one full enriched UniProt V2 release for exact one-hop
// lineage-key matches.
//
// Inputs:
//   * release-specific scan index from 04D1;
//   * full-release OX taxonomy map from 04D0b;
//   * full enriched V2 FASTA.
//
// The scan index already contains the target-specific primary taxonomy bucket.
// A record is considered for a target only when its resolved taxonomy exposes
// the same S:<species> or T:<resolved_taxid> bucket.
//
// This program emits EVIDENCE ONLY.  It never decides SAME_ENTITY, never
// performs sequence similarity, never creates new lineage keys, and never
// recursively expands from a matched record.
//
// Output is long-form: one row per
// target x V2 record x matched frozen key x record-side match role.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

struct Posting {
    std::string target_id;
    std::string primary_bucket;
    std::uint32_t multiplicity = 0;
    bool collision = false;
    bool from_target_accession = false;
    bool from_start_anchor = false;
    bool from_end_anchor = false;
    std::string source_roles;
};

struct OxContext {
    std::string resolved_taxid;
    std::string species_taxid;
    std::string species_bucket;
    std::string resolved_bucket;
};

struct HeaderMeta {
    std::string primary;
    std::string entry;
    std::string rid;
    std::string ox;
    std::string frag;
    std::string len;
    std::string pe;
    std::string sv;
    std::string sec;
    std::string gene;
};

struct EvidenceToken {
    std::string key_type;
    std::string key_value;
    std::string record_match_role;
};

static std::vector<std::string> split_tab(const std::string& line) {
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

static std::string token_value(const std::string& header, const std::string& key) {
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

static HeaderMeta parse_header(const std::string& header) {
    if (header.empty() || header[0] != '>') {
        throw std::runtime_error("malformed V2 header");
    }
    const auto p1 = header.find('|');
    const auto p2 = p1 == std::string::npos
        ? std::string::npos
        : header.find('|', p1 + 1);
    if (p1 == std::string::npos || p2 == std::string::npos || p2 == p1 + 1) {
        throw std::runtime_error("malformed V2 UniProt identifier");
    }

    HeaderMeta m;
    m.primary = header.substr(p1 + 1, p2 - p1 - 1);
    const auto entry_end = header.find_first_of(" \t", p2 + 1);
    m.entry = header.substr(
        p2 + 1,
        (entry_end == std::string::npos ? header.size() : entry_end) - (p2 + 1)
    );
    m.rid = token_value(header, "RID");
    m.ox = token_value(header, "OX");
    m.frag = token_value(header, "FRAG");
    m.len = token_value(header, "LEN");
    m.pe = token_value(header, "PE");
    m.sv = token_value(header, "SV");
    m.sec = token_value(header, "SEC");
    m.gene = token_value(header, "GENE");

    if (m.rid.empty() || m.ox.empty() || m.frag.empty() || m.len.empty()) {
        throw std::runtime_error("V2 header missing required RID/OX/FRAG/LEN");
    }
    return m;
}

static std::vector<std::string> split_comma_unique(const std::string& value) {
    std::vector<std::string> out;
    if (value.empty()) return out;
    std::unordered_set<std::string> seen;
    std::size_t start = 0;
    while (start <= value.size()) {
        const auto p = value.find(',', start);
        const auto end = p == std::string::npos ? value.size() : p;
        if (end > start) {
            std::string item = value.substr(start, end - start);
            if (seen.insert(item).second) out.push_back(std::move(item));
        }
        if (p == std::string::npos) break;
        start = p + 1;
    }
    return out;
}

static std::vector<std::string>
gene_values_unique(const std::string& gene, char wanted_kind) {
    std::vector<std::string> out;
    if (gene.empty()) return out;

    std::unordered_set<std::string> seen;
    std::size_t group_start = 0;

    while (group_start <= gene.size()) {
        const auto group_sep = gene.find('|', group_start);
        const auto group_end =
            group_sep == std::string::npos ? gene.size() : group_sep;

        std::size_t field_start = group_start;
        while (field_start <= group_end) {
            const auto semi = gene.find(';', field_start);
            const auto field_end =
                (semi == std::string::npos || semi > group_end)
                    ? group_end
                    : semi;

            if (
                field_end >= field_start + 2 &&
                gene[field_start] == wanted_kind &&
                gene[field_start + 1] == ':'
            ) {
                const auto value_start = field_start + 2;
                const std::string payload =
                    gene.substr(value_start, field_end - value_start);
                for (auto& value : split_comma_unique(payload)) {
                    if (seen.insert(value).second) out.push_back(std::move(value));
                }
            }

            if (semi == std::string::npos || semi >= group_end) break;
            field_start = semi + 1;
        }

        if (group_sep == std::string::npos) break;
        group_start = group_sep + 1;
    }
    return out;
}

static std::string make_index_key(
    const std::string& bucket,
    const std::string& key_type,
    const std::string& key_value
) {
    return bucket + '\x1f' + key_type + '\x1f' + key_value;
}

using PostingIndex = std::unordered_map<std::string, std::vector<Posting>>;

static PostingIndex load_scan_index(
    const std::string& path,
    const std::string& release,
    std::uint64_t& posting_count
) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open scan index: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty scan index");

    const auto h = header_index(split_tab(line));
    const auto i_release = require_column(h, "release");
    const auto i_tid = require_column(h, "target_id");
    const auto i_bucket = require_column(h, "primary_bucket");
    const auto i_type = require_column(h, "key_type");
    const auto i_value = require_column(h, "key_value");
    const auto i_mult = require_column(h, "key_target_multiplicity");
    const auto i_collision = require_column(h, "key_collision");
    const auto i_target = require_column(h, "from_target_accession");
    const auto i_start = require_column(h, "from_start_anchor");
    const auto i_end = require_column(h, "from_end_anchor");
    const auto i_roles = require_column(h, "source_roles");

    PostingIndex index;
    posting_count = 0;

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        const auto max_i = std::max({
            i_release, i_tid, i_bucket, i_type, i_value, i_mult,
            i_collision, i_target, i_start, i_end, i_roles
        });
        if (f.size() <= max_i) {
            throw std::runtime_error("malformed scan-index row");
        }
        if (f[i_release] != release) continue;

        Posting p;
        p.target_id = f[i_tid];
        p.primary_bucket = f[i_bucket];
        p.multiplicity = static_cast<std::uint32_t>(std::stoul(f[i_mult]));
        p.collision = f[i_collision] == "1";
        p.from_target_accession = f[i_target] == "1";
        p.from_start_anchor = f[i_start] == "1";
        p.from_end_anchor = f[i_end] == "1";
        p.source_roles = f[i_roles];

        index[
            make_index_key(f[i_bucket], f[i_type], f[i_value])
        ].push_back(std::move(p));
        ++posting_count;
    }

    if (posting_count == 0) {
        throw std::runtime_error("no scan-index postings loaded for release " + release);
    }
    return index;
}

static std::unordered_map<std::string, OxContext>
load_ox_context(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open full OX taxonomy: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty OX taxonomy");

    const auto h = header_index(split_tab(line));
    const auto i_ox = require_column(h, "raw_ox");
    const auto i_res = require_column(h, "resolved_taxid");
    const auto i_species = require_column(h, "species_taxid");
    const auto i_sb = require_column(h, "species_bucket");
    const auto i_tb = require_column(h, "resolved_bucket");

    std::unordered_map<std::string, OxContext> out;

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        const auto max_i = std::max({i_ox, i_res, i_species, i_sb, i_tb});
        if (f.size() <= max_i) {
            throw std::runtime_error("malformed full OX taxonomy row");
        }
        if (!out.emplace(
                f[i_ox],
                OxContext{f[i_res], f[i_species], f[i_sb], f[i_tb]}
            ).second) {
            throw std::runtime_error("duplicate raw_ox in taxonomy map: " + f[i_ox]);
        }
    }
    return out;
}

static std::vector<EvidenceToken> evidence_tokens(const HeaderMeta& m) {
    std::vector<EvidenceToken> out;
    out.push_back(EvidenceToken{
        "ACCESSION", m.primary, "ACCESSION_PRIMARY"
    });

    for (const auto& sec : split_comma_unique(m.sec)) {
        out.push_back(EvidenceToken{
            "ACCESSION", sec, "ACCESSION_SECONDARY"
        });
    }
    for (const auto& value : gene_values_unique(m.gene, 'L')) {
        out.push_back(EvidenceToken{
            "ORDERED_LOCUS", value, "ORDERED_LOCUS"
        });
    }
    for (const auto& value : gene_values_unique(m.gene, 'O')) {
        out.push_back(EvidenceToken{
            "ORF", value, "ORF"
        });
    }
    return out;
}

int main(int argc, char** argv) {
    try {
        std::string release;
        std::string scan_index_path;
        std::string ox_taxonomy_path;
        std::string fasta_path;
        std::string output_path;
        std::string summary_path;
        std::uint64_t progress_every = 10'000'000;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) {
                    throw std::runtime_error("missing value for " + arg);
                }
                return argv[++i];
            };

            if (arg == "--release") release = next();
            else if (arg == "--scan-index") scan_index_path = next();
            else if (arg == "--ox-taxonomy") ox_taxonomy_path = next();
            else if (arg == "--fasta") fasta_path = next();
            else if (arg == "--output") output_path = next();
            else if (arg == "--summary") summary_path = next();
            else if (arg == "--progress-every") progress_every = std::stoull(next());
            else throw std::runtime_error("unknown argument: " + arg);
        }

        if (
            release.empty() || scan_index_path.empty() ||
            ox_taxonomy_path.empty() || fasta_path.empty() ||
            output_path.empty() || summary_path.empty()
        ) {
            throw std::runtime_error(
                "required: --release --scan-index --ox-taxonomy "
                "--fasta --output --summary"
            );
        }

        std::uint64_t postings_loaded = 0;
        const auto index =
            load_scan_index(scan_index_path, release, postings_loaded);
        const auto ox_map = load_ox_context(ox_taxonomy_path);

        std::ifstream in(fasta_path);
        if (!in) throw std::runtime_error("cannot open V2 FASTA: " + fasta_path);

        const std::string out_tmp = output_path + ".tmp";
        const std::string sum_tmp = summary_path + ".tmp";

        std::ofstream out(out_tmp);
        if (!out) throw std::runtime_error("cannot create output: " + out_tmp);

        out
            << "release\trecord_number\ttarget_id\ttarget_primary_bucket"
            << "\tkey_type\tkey_value\trecord_match_role"
            << "\tkey_target_multiplicity\tkey_collision"
            << "\tfrom_target_accession\tfrom_start_anchor\tfrom_end_anchor"
            << "\tsource_roles"
            << "\tcandidate_primary_accession\tentry\trid\traw_ox"
            << "\tresolved_taxid\tspecies_taxid\tfrag\tsequence_length"
            << "\tpe\tsv\tsec\tgene\n";

        std::uint64_t records = 0;
        std::uint64_t records_missing_ox_map = 0;
        std::uint64_t records_without_candidate_bucket = 0;
        std::uint64_t records_with_match = 0;
        std::uint64_t evidence_rows = 0;
        std::uint64_t unique_target_record_pairs = 0;
        std::uint64_t collision_evidence_rows = 0;
        std::uint64_t frag_evidence_rows = 0;
        std::uint64_t max_evidence_rows_one_record = 0;
        std::uint64_t max_targets_one_record = 0;

        std::map<std::string, std::uint64_t> key_type_counts;
        std::map<std::string, std::uint64_t> role_counts;
        std::unordered_set<std::string> targets_with_match;

        std::string header, sequence;
        while (std::getline(in, header)) {
            if (!header.empty() && header.back() == '\r') header.pop_back();
            if (header.empty() || header[0] != '>') {
                throw std::runtime_error("V2 format violation: expected header");
            }
            if (!std::getline(in, sequence)) {
                throw std::runtime_error("V2 format violation: missing sequence line");
            }
            if (!sequence.empty() && sequence.back() == '\r') sequence.pop_back();
            if (sequence.empty() || sequence[0] == '>') {
                throw std::runtime_error("V2 format violation: invalid sequence line");
            }

            ++records;
            const auto m = parse_header(header);

            const auto oit = ox_map.find(m.ox);
            if (oit == ox_map.end()) {
                ++records_missing_ox_map;
                continue;
            }

            std::vector<std::string> buckets;
            if (!oit->second.species_bucket.empty()) {
                buckets.push_back(oit->second.species_bucket);
            }
            if (
                !oit->second.resolved_bucket.empty() &&
                oit->second.resolved_bucket != oit->second.species_bucket
            ) {
                buckets.push_back(oit->second.resolved_bucket);
            }
            if (buckets.empty()) {
                ++records_without_candidate_bucket;
                continue;
            }

            const auto tokens = evidence_tokens(m);
            std::unordered_set<std::string> record_targets;
            std::uint64_t record_evidence_rows = 0;

            for (const auto& bucket : buckets) {
                for (const auto& token : tokens) {
                    const auto iit = index.find(
                        make_index_key(bucket, token.key_type, token.key_value)
                    );
                    if (iit == index.end()) continue;

                    for (const auto& p : iit->second) {
                        out
                            << release << '\t'
                            << records << '\t'
                            << p.target_id << '\t'
                            << p.primary_bucket << '\t'
                            << token.key_type << '\t'
                            << token.key_value << '\t'
                            << token.record_match_role << '\t'
                            << p.multiplicity << '\t'
                            << (p.collision ? 1 : 0) << '\t'
                            << (p.from_target_accession ? 1 : 0) << '\t'
                            << (p.from_start_anchor ? 1 : 0) << '\t'
                            << (p.from_end_anchor ? 1 : 0) << '\t'
                            << p.source_roles << '\t'
                            << m.primary << '\t'
                            << m.entry << '\t'
                            << m.rid << '\t'
                            << m.ox << '\t'
                            << oit->second.resolved_taxid << '\t'
                            << oit->second.species_taxid << '\t'
                            << m.frag << '\t'
                            << m.len << '\t'
                            << m.pe << '\t'
                            << m.sv << '\t'
                            << m.sec << '\t'
                            << m.gene << '\n';

                        ++evidence_rows;
                        ++record_evidence_rows;
                        ++key_type_counts[token.key_type];
                        ++role_counts[token.record_match_role];
                        if (p.collision) ++collision_evidence_rows;
                        if (m.frag == "1") ++frag_evidence_rows;
                        record_targets.insert(p.target_id);
                        targets_with_match.insert(p.target_id);
                    }
                }
            }

            if (record_evidence_rows > 0) {
                ++records_with_match;
                unique_target_record_pairs += record_targets.size();
                max_evidence_rows_one_record =
                    std::max(max_evidence_rows_one_record, record_evidence_rows);
                max_targets_one_record =
                    std::max<std::uint64_t>(
                        max_targets_one_record,
                        record_targets.size()
                    );
            }

            if (progress_every && records % progress_every == 0) {
                std::cerr
                    << "[04D2 " << release << "] records=" << records
                    << " matched_records=" << records_with_match
                    << " target_record_pairs=" << unique_target_record_pairs
                    << " evidence_rows=" << evidence_rows
                    << '\n';
            }
        }

        out.close();
        if (!out) throw std::runtime_error("write failure: " + out_tmp);

        {
            std::ofstream s(sum_tmp);
            if (!s) throw std::runtime_error("cannot create summary: " + sum_tmp);
            s << "metric\tvalue\n";
            s << "release\t" << release << '\n';
            s << "scan_index_postings_loaded\t" << postings_loaded << '\n';
            s << "full_ox_taxonomy_count\t" << ox_map.size() << '\n';
            s << "full_v2_record_count\t" << records << '\n';
            s << "records_missing_ox_taxonomy\t" << records_missing_ox_map << '\n';
            s << "records_without_candidate_bucket\t"
              << records_without_candidate_bucket << '\n';
            s << "records_with_lineage_match\t" << records_with_match << '\n';
            s << "unique_target_record_pairs\t" << unique_target_record_pairs << '\n';
            s << "targets_with_lineage_match\t" << targets_with_match.size() << '\n';
            s << "evidence_rows\t" << evidence_rows << '\n';
            s << "collision_evidence_rows\t" << collision_evidence_rows << '\n';
            s << "fragment_evidence_rows\t" << frag_evidence_rows << '\n';
            s << "max_evidence_rows_for_one_record\t"
              << max_evidence_rows_one_record << '\n';
            s << "max_targets_for_one_record\t" << max_targets_one_record << '\n';
            for (const auto& kv : key_type_counts) {
                s << "evidence_key_type_" << kv.first << '\t' << kv.second << '\n';
            }
            for (const auto& kv : role_counts) {
                s << "evidence_record_role_" << kv.first << '\t'
                  << kv.second << '\n';
            }
        }

        if (std::rename(out_tmp.c_str(), output_path.c_str()) != 0) {
            throw std::runtime_error("failed to install evidence output");
        }
        if (std::rename(sum_tmp.c_str(), summary_path.c_str()) != 0) {
            throw std::runtime_error("failed to install summary");
        }

        std::cerr
            << "[04D2 " << release << "] COMPLETE records=" << records
            << " matched_records=" << records_with_match
            << " target_record_pairs=" << unique_target_record_pairs
            << " evidence_rows=" << evidence_rows
            << '\n';

        return 0;

    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
