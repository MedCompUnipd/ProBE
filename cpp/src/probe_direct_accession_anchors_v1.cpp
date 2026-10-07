// ProBE production direct accession anchor scanner v1
// Stage 04A: find direct accession anchors for canonical targets in one full
// enriched UniProt V2 release.
//
// No biological identity decision and no sequence search are performed here.
// A direct anchor is only a V2 record whose PRIMARY or SEC accession matches
// target.optional_accession exactly. Cross-species and FRAG=1 anchors are kept
// as facts for later classification.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

struct TargetRef {
    std::string target_id;
    std::string optional_accession;
};

struct HeaderMeta {
    std::string primary, entry, rid, src, rec, ox, frag, len, pe, sv, sec, gene;
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
        ? std::string::npos : header.find('|', p1 + 1);
    if (p1 == std::string::npos || p2 == std::string::npos || p2 == p1 + 1) {
        throw std::runtime_error("malformed V2 identifier");
    }

    HeaderMeta m;
    m.primary = header.substr(p1 + 1, p2 - p1 - 1);
    const auto entry_end = header.find_first_of(" \t", p2 + 1);
    m.entry = header.substr(
        p2 + 1,
        (entry_end == std::string::npos ? header.size() : entry_end) - (p2 + 1)
    );
    m.rid = token_value(header, "RID");
    m.src = token_value(header, "SRC");
    m.rec = token_value(header, "REC");
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

static std::vector<std::string> split_comma(const std::string& value) {
    std::vector<std::string> out;
    if (value.empty()) return out;
    std::size_t start = 0;
    while (start <= value.size()) {
        const auto p = value.find(',', start);
        const auto end = p == std::string::npos ? value.size() : p;
        if (end > start) out.emplace_back(value.substr(start, end - start));
        if (p == std::string::npos) break;
        start = p + 1;
    }
    return out;
}

static std::unordered_map<std::string, std::vector<TargetRef>>
load_target_accessions(
    const std::string& path,
    std::uint64_t& target_count,
    std::uint64_t& target_with_accession_count
) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open targets TSV: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty targets TSV");
    const auto h = header_index(split_tab(line));
    const auto i_id = require_column(h, "target_id");
    const auto i_acc = require_column(h, "optional_accession");

    std::unordered_map<std::string, std::vector<TargetRef>> by_acc;
    std::unordered_set<std::string> seen_ids;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        const auto f = split_tab(line);
        if (f.size() <= std::max(i_id, i_acc)) {
            throw std::runtime_error("malformed target TSV row");
        }
        if (!seen_ids.insert(f[i_id]).second) {
            throw std::runtime_error("duplicate target_id: " + f[i_id]);
        }
        ++target_count;
        if (f[i_acc].empty()) continue;
        ++target_with_accession_count;
        by_acc[f[i_acc]].push_back(TargetRef{f[i_id], f[i_acc]});
    }
    return by_acc;
}

int main(int argc, char** argv) {
    try {
        std::string targets_path, fasta_path, release, output_path, summary_path;
        std::uint64_t progress_every = 10'000'000;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) throw std::runtime_error("missing value for " + arg);
                return argv[++i];
            };
            if (arg == "--targets") targets_path = next();
            else if (arg == "--fasta") fasta_path = next();
            else if (arg == "--release") release = next();
            else if (arg == "--output") output_path = next();
            else if (arg == "--summary") summary_path = next();
            else if (arg == "--progress-every") progress_every = std::stoull(next());
            else throw std::runtime_error("unknown argument: " + arg);
        }

        if (targets_path.empty() || fasta_path.empty() || release.empty() ||
            output_path.empty() || summary_path.empty()) {
            throw std::runtime_error(
                "required: --targets --fasta --release --output --summary"
            );
        }

        std::uint64_t target_count = 0, target_with_acc = 0;
        const auto targets_by_acc = load_target_accessions(
            targets_path, target_count, target_with_acc
        );

        std::ifstream in(fasta_path);
        if (!in) throw std::runtime_error("cannot open V2 FASTA: " + fasta_path);

        const std::string out_tmp = output_path + ".tmp";
        const std::string sum_tmp = summary_path + ".tmp";
        std::ofstream out(out_tmp);
        if (!out) throw std::runtime_error("cannot create output: " + out_tmp);

        out << "release\trecord_number\ttarget_id\ttarget_optional_accession"
            << "\tdirect_match_type\tdirect_match_accession"
            << "\tprimary_accession\tentry\trid\tsrc\trec\traw_ox"
            << "\tfrag\tsequence_length\tpe\tsv\tsec\tgene\n";

        std::uint64_t records = 0, anchor_rows = 0;
        std::uint64_t primary_rows = 0, secondary_rows = 0;
        std::unordered_set<std::string> targets_with_anchor;
        std::unordered_map<std::string, std::uint64_t> rows_per_target;

        std::string header, sequence;
        while (std::getline(in, header)) {
            if (!header.empty() && header.back() == '\r') header.pop_back();
            if (header.empty() || header[0] != '>') {
                throw std::runtime_error("V2 format violation: expected header");
            }
            if (!std::getline(in, sequence)) {
                throw std::runtime_error("V2 format violation: missing sequence");
            }
            if (!sequence.empty() && sequence.back() == '\r') sequence.pop_back();
            if (sequence.empty() || sequence[0] == '>') {
                throw std::runtime_error("V2 format violation: invalid sequence");
            }

            ++records;
            const auto m = parse_header(header);

            auto emit = [&](const TargetRef& t, const char* type, const std::string& acc) {
                out << release << '\t' << records << '\t'
                    << t.target_id << '\t' << t.optional_accession << '\t'
                    << type << '\t' << acc << '\t'
                    << m.primary << '\t' << m.entry << '\t' << m.rid << '\t'
                    << m.src << '\t' << m.rec << '\t' << m.ox << '\t'
                    << m.frag << '\t' << m.len << '\t' << m.pe << '\t'
                    << m.sv << '\t' << m.sec << '\t' << m.gene << '\n';
                ++anchor_rows;
                targets_with_anchor.insert(t.target_id);
                ++rows_per_target[t.target_id];
            };

            const auto pit = targets_by_acc.find(m.primary);
            if (pit != targets_by_acc.end()) {
                for (const auto& t : pit->second) {
                    emit(t, "PRIMARY", m.primary);
                    ++primary_rows;
                }
            }

            for (const auto& sec : split_comma(m.sec)) {
                const auto sit = targets_by_acc.find(sec);
                if (sit == targets_by_acc.end()) continue;
                for (const auto& t : sit->second) {
                    emit(t, "SECONDARY", sec);
                    ++secondary_rows;
                }
            }

            if (progress_every && records % progress_every == 0) {
                std::cerr << "[04A " << release << "] records=" << records
                          << " anchor_rows=" << anchor_rows
                          << " targets_with_anchor=" << targets_with_anchor.size()
                          << '\n';
            }
        }
        out.close();
        if (!out) throw std::runtime_error("write failure: " + out_tmp);

        std::uint64_t multiple = 0, max_rows = 0;
        for (const auto& kv : rows_per_target) {
            if (kv.second > 1) ++multiple;
            max_rows = std::max(max_rows, kv.second);
        }

        {
            std::ofstream s(sum_tmp);
            if (!s) throw std::runtime_error("cannot create summary: " + sum_tmp);
            s << "metric\tvalue\n";
            s << "release\t" << release << '\n';
            s << "target_count\t" << target_count << '\n';
            s << "targets_with_optional_accession\t" << target_with_acc << '\n';
            s << "distinct_target_optional_accessions\t" << targets_by_acc.size() << '\n';
            s << "full_v2_record_count\t" << records << '\n';
            s << "anchor_rows\t" << anchor_rows << '\n';
            s << "primary_match_rows\t" << primary_rows << '\n';
            s << "secondary_match_rows\t" << secondary_rows << '\n';
            s << "targets_with_direct_anchor\t" << targets_with_anchor.size() << '\n';
            s << "targets_without_direct_anchor\t"
              << (target_with_acc - targets_with_anchor.size()) << '\n';
            s << "targets_with_multiple_anchor_rows\t" << multiple << '\n';
            s << "max_anchor_rows_per_target\t" << max_rows << '\n';
        }

        if (std::rename(out_tmp.c_str(), output_path.c_str()) != 0) {
            throw std::runtime_error("failed to install output");
        }
        if (std::rename(sum_tmp.c_str(), summary_path.c_str()) != 0) {
            throw std::runtime_error("failed to install summary");
        }

        std::cerr << "[04A " << release << "] COMPLETE records=" << records
                  << " anchor_rows=" << anchor_rows
                  << " targets_with_anchor=" << targets_with_anchor.size()
                  << '\n';
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
