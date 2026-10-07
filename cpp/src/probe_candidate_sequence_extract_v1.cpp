// Production ProBE candidate pipeline.
// Stage 05D: extract one deduplicated candidate sequence store per UniProt
// release from the FULL enriched V2 FASTA.
//
// Inputs:
//   * Stage 05A2 final primary candidate union
//   * Stage 05C2 final taxonomy audit channel
//   * one full enriched V2 release FASTA
//
// Included RIDs:
//   * every primary candidate RID for the requested release
//   * only Stage-05C rows with audit_alignment_allowed=1
//
// Unit of output: one row per unique UniProt RID.
//
// This stage performs NO alignment, NO candidate filtering and NO SAME_ENTITY
// decision.  It validates that record metadata expected by 05A/05C agrees with
// the full V2 record and that LEN equals the physical sequence length.

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

struct ExpectedMeta {
    std::string primary;
    std::string entry;
    std::string raw_ox;
    std::string frag;
    std::string len;
    std::string pe;
    std::string sv;
    std::string sec;
    std::string gene;

    std::uint64_t primary_reference_count = 0;
    std::uint64_t audit_reference_count = 0;
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
    for (std::size_t i = 0; i < fields.size(); ++i) {
        out.emplace(fields[i], i);
    }
    return out;
}

static std::size_t require_column(
    const std::unordered_map<std::string, std::size_t>& h,
    const std::string& name
) {
    const auto it = h.find(name);
    if (it == h.end()) {
        throw std::runtime_error("missing TSV column: " + name);
    }
    return it->second;
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

static HeaderMeta parse_header(const std::string& header) {
    if (header.empty() || header[0] != '>') {
        throw std::runtime_error("malformed V2 header");
    }

    const auto p1 = header.find('|');
    const auto p2 = p1 == std::string::npos
        ? std::string::npos
        : header.find('|', p1 + 1);

    if (
        p1 == std::string::npos ||
        p2 == std::string::npos ||
        p2 == p1 + 1
    ) {
        throw std::runtime_error("malformed V2 UniProt identifier");
    }

    HeaderMeta m;
    m.primary = header.substr(p1 + 1, p2 - p1 - 1);

    const auto entry_end = header.find_first_of(" \t", p2 + 1);
    m.entry = header.substr(
        p2 + 1,
        (entry_end == std::string::npos ? header.size() : entry_end)
            - (p2 + 1)
    );

    m.rid = token_value(header, "RID");
    m.ox = token_value(header, "OX");
    m.frag = token_value(header, "FRAG");
    m.len = token_value(header, "LEN");
    m.pe = token_value(header, "PE");
    m.sv = token_value(header, "SV");
    m.sec = token_value(header, "SEC");
    m.gene = token_value(header, "GENE");

    if (
        m.rid.empty() ||
        m.ox.empty() ||
        m.frag.empty() ||
        m.len.empty()
    ) {
        throw std::runtime_error(
            "V2 header missing required RID/OX/FRAG/LEN"
        );
    }

    return m;
}

static std::uint64_t parse_u64(
    const std::string& value,
    const std::string& what
) {
    if (value.empty()) {
        throw std::runtime_error("empty integer field: " + what);
    }

    std::size_t pos = 0;
    const auto parsed = std::stoull(value, &pos);
    if (pos != value.size()) {
        throw std::runtime_error(
            "invalid integer field " + what + ": " + value
        );
    }
    return parsed;
}

static void merge_text(
    std::string& stored,
    const std::string& incoming,
    const std::string& field,
    std::uint64_t rid
) {
    if (stored.empty()) {
        stored = incoming;
        return;
    }

    if (stored != incoming) {
        throw std::runtime_error(
            "conflicting expected metadata for RID=" +
            std::to_string(rid) +
            " field=" + field +
            ": " + stored + " != " + incoming
        );
    }
}

static void merge_expected(
    ExpectedMeta& e,
    const std::string& primary,
    const std::string& entry,
    const std::string& raw_ox,
    const std::string& frag,
    const std::string& len,
    const std::string& pe,
    const std::string& sv,
    const std::string& sec,
    const std::string& gene,
    std::uint64_t rid
) {
    merge_text(e.primary, primary, "primary_accession", rid);
    merge_text(e.entry, entry, "entry", rid);
    merge_text(e.raw_ox, raw_ox, "raw_ox", rid);
    merge_text(e.frag, frag, "frag", rid);
    merge_text(e.len, len, "sequence_length", rid);

    // PE/SV/SEC/GENE can legitimately be empty strings, so initialize them
    // from the first reference and compare explicitly thereafter.
    if (e.primary_reference_count + e.audit_reference_count == 0) {
        e.pe = pe;
        e.sv = sv;
        e.sec = sec;
        e.gene = gene;
    } else {
        if (e.pe != pe) {
            throw std::runtime_error(
                "conflicting expected PE for RID=" + std::to_string(rid)
            );
        }
        if (e.sv != sv) {
            throw std::runtime_error(
                "conflicting expected SV for RID=" + std::to_string(rid)
            );
        }
        if (e.sec != sec) {
            throw std::runtime_error(
                "conflicting expected SEC for RID=" + std::to_string(rid)
            );
        }
        if (e.gene != gene) {
            throw std::runtime_error(
                "conflicting expected GENE for RID=" + std::to_string(rid)
            );
        }
    }
}

using RequestMap = std::unordered_map<std::uint64_t, ExpectedMeta>;

static std::uint64_t load_primary_requests(
    const std::string& path,
    const std::string& release,
    RequestMap& requests
) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("cannot open candidate union: " + path);
    }

    std::string line;
    if (!std::getline(in, line)) {
        throw std::runtime_error("empty candidate union");
    }

    const auto h = header_index(split_tab(line));
    const auto i_release = require_column(h, "release");
    const auto i_rid = require_column(h, "rid");
    const auto i_primary = require_column(h, "candidate_primary_accession");
    const auto i_entry = require_column(h, "entry");
    const auto i_ox = require_column(h, "raw_ox");
    const auto i_frag = require_column(h, "frag");
    const auto i_len = require_column(h, "sequence_length");
    const auto i_pe = require_column(h, "pe");
    const auto i_sv = require_column(h, "sv");
    const auto i_sec = require_column(h, "sec");
    const auto i_gene = require_column(h, "gene");

    const auto max_i = std::max({
        i_release, i_rid, i_primary, i_entry, i_ox, i_frag,
        i_len, i_pe, i_sv, i_sec, i_gene
    });

    std::uint64_t selected_rows = 0;

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= max_i) {
            throw std::runtime_error("malformed candidate-union row");
        }
        if (f[i_release] != release) continue;

        const auto rid = parse_u64(f[i_rid], "candidate union RID");
        auto& e = requests[rid];

        merge_expected(
            e,
            f[i_primary],
            f[i_entry],
            f[i_ox],
            f[i_frag],
            f[i_len],
            f[i_pe],
            f[i_sv],
            f[i_sec],
            f[i_gene],
            rid
        );
        ++e.primary_reference_count;
        ++selected_rows;
    }

    return selected_rows;
}

static std::uint64_t load_audit_requests(
    const std::string& path,
    const std::string& release,
    RequestMap& requests
) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("cannot open taxonomy audit: " + path);
    }

    std::string line;
    if (!std::getline(in, line)) {
        throw std::runtime_error("empty taxonomy audit");
    }

    const auto h = header_index(split_tab(line));
    const auto i_release = require_column(h, "release");
    const auto i_allowed = require_column(h, "audit_alignment_allowed");
    const auto i_rid = require_column(h, "rid");
    const auto i_primary = require_column(h, "candidate_primary_accession");
    const auto i_entry = require_column(h, "entry");
    const auto i_ox = require_column(h, "raw_ox");
    const auto i_frag = require_column(h, "frag");
    const auto i_len = require_column(h, "sequence_length");
    const auto i_pe = require_column(h, "pe");
    const auto i_sv = require_column(h, "sv");
    const auto i_sec = require_column(h, "sec");
    const auto i_gene = require_column(h, "gene");

    const auto max_i = std::max({
        i_release, i_allowed, i_rid, i_primary, i_entry, i_ox,
        i_frag, i_len, i_pe, i_sv, i_sec, i_gene
    });

    std::uint64_t selected_rows = 0;

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= max_i) {
            throw std::runtime_error("malformed taxonomy-audit row");
        }
        if (f[i_release] != release) continue;
        if (f[i_allowed] != "1") continue;

        const auto rid = parse_u64(f[i_rid], "taxonomy audit RID");
        auto& e = requests[rid];

        merge_expected(
            e,
            f[i_primary],
            f[i_entry],
            f[i_ox],
            f[i_frag],
            f[i_len],
            f[i_pe],
            f[i_sv],
            f[i_sec],
            f[i_gene],
            rid
        );
        ++e.audit_reference_count;
        ++selected_rows;
    }

    return selected_rows;
}

static void require_equal(
    const std::string& expected,
    const std::string& observed,
    const std::string& field,
    std::uint64_t rid
) {
    if (expected != observed) {
        throw std::runtime_error(
            "full-V2 metadata mismatch RID=" + std::to_string(rid) +
            " field=" + field +
            " expected=" + expected +
            " observed=" + observed
        );
    }
}

int main(int argc, char** argv) {
    try {
        std::string release;
        std::string union_path;
        std::string audit_path;
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
            else if (arg == "--candidate-union") union_path = next();
            else if (arg == "--taxonomy-audit") audit_path = next();
            else if (arg == "--fasta") fasta_path = next();
            else if (arg == "--output") output_path = next();
            else if (arg == "--summary") summary_path = next();
            else if (arg == "--progress-every") {
                progress_every = parse_u64(next(), "progress-every");
            } else {
                throw std::runtime_error("unknown argument: " + arg);
            }
        }

        if (
            release.empty() ||
            union_path.empty() ||
            audit_path.empty() ||
            fasta_path.empty() ||
            output_path.empty() ||
            summary_path.empty()
        ) {
            throw std::runtime_error(
                "required: --release --candidate-union --taxonomy-audit "
                "--fasta --output --summary"
            );
        }

        RequestMap requests;
        requests.reserve(1'000'000);

        const auto primary_reference_rows =
            load_primary_requests(union_path, release, requests);
        const auto audit_reference_rows =
            load_audit_requests(audit_path, release, requests);

        if (requests.empty()) {
            throw std::runtime_error(
                "no requested RIDs for release " + release
            );
        }

        std::uint64_t primary_unique = 0;
        std::uint64_t audit_unique = 0;
        std::uint64_t overlap_unique = 0;
        std::uint64_t max_requested_rid = 0;

        for (const auto& kv : requests) {
            const auto rid = kv.first;
            const auto& e = kv.second;

            if (e.primary_reference_count > 0) ++primary_unique;
            if (e.audit_reference_count > 0) ++audit_unique;
            if (
                e.primary_reference_count > 0 &&
                e.audit_reference_count > 0
            ) {
                ++overlap_unique;
            }

            max_requested_rid = std::max(max_requested_rid, rid);
        }

        std::ifstream in(fasta_path);
        if (!in) {
            throw std::runtime_error("cannot open full V2 FASTA: " + fasta_path);
        }

        const std::string output_tmp = output_path + ".tmp";
        const std::string summary_tmp = summary_path + ".tmp";

        std::ofstream out(output_tmp);
        if (!out) {
            throw std::runtime_error("cannot create output: " + output_tmp);
        }

        out
            << "release\tfull_v2_record_number\trid"
            << "\tprimary_accession\tentry\traw_ox\tfrag"
            << "\tsequence_length\tpe\tsv\tsec\tgene"
            << "\tneeded_by_primary\tneeded_by_taxonomy_audit"
            << "\tprimary_reference_count\taudit_reference_count"
            << "\tsequence\n";

        std::uint64_t records_scanned = 0;
        std::uint64_t found = 0;
        std::uint64_t total_residues = 0;
        std::uint64_t max_sequence_length = 0;

        std::string header;
        std::string sequence;

        while (
            records_scanned < max_requested_rid &&
            std::getline(in, header)
        ) {
            if (!header.empty() && header.back() == '\r') {
                header.pop_back();
            }

            if (header.empty() || header[0] != '>') {
                throw std::runtime_error(
                    "V2 format violation: expected header at record " +
                    std::to_string(records_scanned + 1)
                );
            }

            if (!std::getline(in, sequence)) {
                throw std::runtime_error(
                    "V2 format violation: missing sequence line"
                );
            }
            if (!sequence.empty() && sequence.back() == '\r') {
                sequence.pop_back();
            }
            if (sequence.empty() || sequence[0] == '>') {
                throw std::runtime_error(
                    "V2 format violation: invalid sequence line"
                );
            }

            ++records_scanned;

            const auto req_it = requests.find(records_scanned);
            if (req_it == requests.end()) {
                if (
                    progress_every &&
                    records_scanned % progress_every == 0
                ) {
                    std::cerr
                        << "[05D " << release << "] scanned="
                        << records_scanned
                        << " found=" << found
                        << " requested=" << requests.size()
                        << '\n';
                }
                continue;
            }

            const auto m = parse_header(header);
            const auto header_rid = parse_u64(m.rid, "header RID");

            if (header_rid != records_scanned) {
                throw std::runtime_error(
                    "RID/record ordinal mismatch: record=" +
                    std::to_string(records_scanned) +
                    " header_RID=" + std::to_string(header_rid)
                );
            }

            const auto& e = req_it->second;

            require_equal(e.primary, m.primary, "primary_accession", header_rid);
            require_equal(e.entry, m.entry, "entry", header_rid);
            require_equal(e.raw_ox, m.ox, "raw_ox", header_rid);
            require_equal(e.frag, m.frag, "frag", header_rid);
            require_equal(e.len, m.len, "LEN", header_rid);
            require_equal(e.pe, m.pe, "PE", header_rid);
            require_equal(e.sv, m.sv, "SV", header_rid);
            require_equal(e.sec, m.sec, "SEC", header_rid);
            require_equal(e.gene, m.gene, "GENE", header_rid);

            const auto declared_len = parse_u64(m.len, "header LEN");
            if (declared_len != sequence.size()) {
                throw std::runtime_error(
                    "sequence length mismatch RID=" +
                    std::to_string(header_rid) +
                    " LEN=" + std::to_string(declared_len) +
                    " physical=" + std::to_string(sequence.size())
                );
            }

            out
                << release << '\t'
                << records_scanned << '\t'
                << header_rid << '\t'
                << m.primary << '\t'
                << m.entry << '\t'
                << m.ox << '\t'
                << m.frag << '\t'
                << m.len << '\t'
                << m.pe << '\t'
                << m.sv << '\t'
                << m.sec << '\t'
                << m.gene << '\t'
                << (e.primary_reference_count > 0 ? 1 : 0) << '\t'
                << (e.audit_reference_count > 0 ? 1 : 0) << '\t'
                << e.primary_reference_count << '\t'
                << e.audit_reference_count << '\t'
                << sequence << '\n';

            ++found;
            total_residues += sequence.size();
            max_sequence_length =
                std::max<std::uint64_t>(
                    max_sequence_length,
                    sequence.size()
                );

            if (
                progress_every &&
                records_scanned % progress_every == 0
            ) {
                std::cerr
                    << "[05D " << release << "] scanned="
                    << records_scanned
                    << " found=" << found
                    << " requested=" << requests.size()
                    << '\n';
            }
        }

        out.close();
        if (!out) {
            throw std::runtime_error("write failure: " + output_tmp);
        }

        if (found != requests.size()) {
            throw std::runtime_error(
                "not all requested RIDs were found: requested=" +
                std::to_string(requests.size()) +
                " found=" + std::to_string(found) +
                " max_requested_RID=" +
                std::to_string(max_requested_rid) +
                " records_scanned=" +
                std::to_string(records_scanned)
            );
        }

        {
            std::ofstream s(summary_tmp);
            if (!s) {
                throw std::runtime_error(
                    "cannot create summary: " + summary_tmp
                );
            }

            s << "metric\tvalue\n";
            s << "release\t" << release << '\n';
            s << "primary_reference_rows\t"
              << primary_reference_rows << '\n';
            s << "taxonomy_audit_reference_rows\t"
              << audit_reference_rows << '\n';
            s << "requested_unique_rids\t"
              << requests.size() << '\n';
            s << "primary_unique_rids\t"
              << primary_unique << '\n';
            s << "taxonomy_audit_unique_rids\t"
              << audit_unique << '\n';
            s << "primary_audit_unique_rid_overlap\t"
              << overlap_unique << '\n';
            s << "max_requested_rid\t"
              << max_requested_rid << '\n';
            s << "full_v2_records_scanned\t"
              << records_scanned << '\n';
            s << "found_unique_rids\t"
              << found << '\n';
            s << "missing_requested_rids\t0\n";
            s << "metadata_conflicts\t0\n";
            s << "sequence_length_mismatches\t0\n";
            s << "total_candidate_sequence_residues\t"
              << total_residues << '\n';
            s << "max_candidate_sequence_length\t"
              << max_sequence_length << '\n';
        }

        if (std::rename(output_tmp.c_str(), output_path.c_str()) != 0) {
            throw std::runtime_error(
                "failed to install candidate sequence store"
            );
        }
        if (std::rename(summary_tmp.c_str(), summary_path.c_str()) != 0) {
            throw std::runtime_error(
                "failed to install 05D summary"
            );
        }

        std::cerr
            << "[05D " << release << "] COMPLETE"
            << " requested_unique_rids=" << requests.size()
            << " found=" << found
            << " scanned=" << records_scanned
            << '\n';

        return 0;

    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
