// ProBE 2C3A: read-only FULL-V2 cross-strain exact-match discovery.
// A discovered exact match is NOT by itself an annotation donor.
// Data source: pinned 01A_FULL FASTA, 02B targets, 04D0b FULL-OX taxonomy.
// FRAG=1 and ambiguous sequences cannot enter this discovery channel.

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

struct Target {
    std::string id;
    std::string taxid;
    std::string species;
};

struct Taxon {
    std::string resolved;
    std::string species;
};

using Rows = std::vector<std::string>;
using Index = std::unordered_map<std::string, std::size_t>;

Rows split_tab(const std::string& row) {
    Rows fields;
    std::size_t start = 0;
    for (;;) {
        const auto tab = row.find('\t', start);
        if (tab == std::string::npos) {
            fields.push_back(row.substr(start));
            return fields;
        }
        fields.push_back(row.substr(start, tab - start));
        start = tab + 1;
    }
}

Index fields_index(const std::string& heading) {
    Index result;
    const Rows names = split_tab(heading);
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (!result.emplace(names[i], i).second) {
            throw std::runtime_error("duplicate TSV heading: " + names[i]);
        }
    }
    return result;
}

std::size_t field(const Index& indices, const std::string& name) {
    const auto found = indices.find(name);
    if (found == indices.end()) {
        throw std::runtime_error("missing TSV heading: " + name);
    }
    return found->second;
}

std::string value(const Rows& columns, std::size_t offset) {
    if (offset >= columns.size()) {
        throw std::runtime_error("malformed TSV row");
    }
    return columns[offset];
}

bool valid_sequence(const std::string& sequence) {
    if (sequence.empty()) return false;
    for (const char residue : sequence) {
        switch (residue) {
        case 'A': case 'C': case 'D': case 'E': case 'F':
        case 'G': case 'H': case 'I': case 'K': case 'L':
        case 'M': case 'N': case 'P': case 'Q': case 'R':
        case 'S': case 'T': case 'V': case 'W': case 'Y':
        case 'U': case 'O':
            break;
        default:
            return false;
        }
    }
    return true;
}

std::string token(const std::string& header, const std::string& name) {
    const auto marker = " " + name + "=";
    const auto found = header.find(marker);
    if (found == std::string::npos) return "";
    const auto start = found + marker.size();
    const auto finish = header.find_first_of(" \t", start);
    return header.substr(start, finish - start);
}

std::string accession(const std::string& header) {
    const auto first = header.find('|');
    if (first == std::string::npos) return "";
    const auto second = header.find('|', first + 1);
    if (second == std::string::npos || second == first + 1) return "";
    return header.substr(first + 1, second - first - 1);
}

bool decimal(const std::string& text) {
    if (text.empty()) return false;
    for (const char c : text) {
        if (c < '0' || c > '9') return false;
    }
    return text != "0";
}

using ExactTargets = std::unordered_map<std::string, std::vector<Target>>;

ExactTargets targets(const std::string& sequences, const std::string& taxonomy) {
    std::ifstream seq_stream(sequences);
    if (!seq_stream) throw std::runtime_error("cannot open target sequences");
    std::string line;
    if (!std::getline(seq_stream, line)) {
        throw std::runtime_error("empty target sequences");
    }
    const auto header = fields_index(line);
    const auto id_index = field(header, "target_id");
    const auto seq_index = field(header, "normalized_sequence");
    std::unordered_map<std::string, std::string> sequences_by_id;
    while (std::getline(seq_stream, line)) {
        if (line.empty()) continue;
        const auto parts = split_tab(line);
        const auto id = value(parts, id_index);
        const auto seq = value(parts, seq_index);
        if (id.empty() || !sequences_by_id.emplace(id, seq).second) {
            throw std::runtime_error("empty or duplicate target ID");
        }
    }

    std::ifstream tax_stream(taxonomy);
    if (!tax_stream) throw std::runtime_error("cannot open target taxonomy");
    if (!std::getline(tax_stream, line)) {
        throw std::runtime_error("empty target taxonomy");
    }
    const auto th = fields_index(line);
    const auto tax_id = field(th, "target_id");
    const auto tax_resolved = field(th, "resolved_taxid");
    const auto tax_species = field(th, "species_taxid");
    ExactTargets result;
    std::unordered_map<std::string, bool> seen;
    while (std::getline(tax_stream, line)) {
        if (line.empty()) continue;
        const auto parts = split_tab(line);
        const auto id = value(parts, tax_id);
        const auto sit = sequences_by_id.find(id);
        if (sit == sequences_by_id.end() || !seen.emplace(id, true).second) {
            throw std::runtime_error("extra or duplicate target taxonomy row");
        }
        const auto resolved = value(parts, tax_resolved);
        const auto species = value(parts, tax_species);
        if (!decimal(resolved) || !decimal(species)) continue;
        if (!valid_sequence(sit->second)) continue;
        result[sit->second].push_back(Target{id, resolved, species});
    }
    if (seen.size() != sequences_by_id.size()) {
        throw std::runtime_error("missing target taxonomy rows");
    }
    return result;
}

std::unordered_map<std::string, Taxon> ox_taxonomy(const std::string& source) {
    std::ifstream stream(source);
    if (!stream) throw std::runtime_error("cannot open FULL OX taxonomy");
    std::string line;
    if (!std::getline(stream, line)) {
        throw std::runtime_error("empty FULL OX taxonomy");
    }
    const auto header = fields_index(line);
    const auto ox_field = field(header, "raw_ox");
    const auto tax_field = field(header, "resolved_taxid");
    const auto species_field = field(header, "species_taxid");
    const auto bucket_field = field(header, "species_bucket");
    std::unordered_map<std::string, Taxon> result;
    while (std::getline(stream, line)) {
        if (line.empty()) continue;
        const auto parts = split_tab(line);
        const auto raw = value(parts, ox_field);
        const auto tax = value(parts, tax_field);
        const auto species = value(parts, species_field);
        const auto bucket = value(parts, bucket_field);
        if (!result.emplace(raw, Taxon{tax, species}).second) {
            throw std::runtime_error("duplicate FULL OX mapping");
        }
        if (decimal(tax) && decimal(species) && bucket != "S:" + species) {
            throw std::runtime_error("inconsistent FULL OX species bucket");
        }
    }
    return result;
}

struct Counts {
    std::uint64_t records = 0;
    std::uint64_t no_ox = 0;
    std::uint64_t unknown_ox = 0;
    std::uint64_t unresolved_taxonomy = 0;
    std::uint64_t fragments = 0;
    std::uint64_t ambiguous = 0;
    std::uint64_t matches = 0;
    std::uint64_t matching_records = 0;
};

std::string required_arg(int argc, char** argv, const std::string& wanted) {
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 >= argc) {
            throw std::runtime_error("missing argument value");
        }
        if (argv[i] == wanted) return argv[i + 1];
    }
    throw std::runtime_error("missing argument: " + wanted);
}

void scan(int argc, char** argv) {
    const auto seq_path = required_arg(argc, argv, "--targets");
    const auto target_tax = required_arg(argc, argv, "--target-taxonomy");
    const auto candidate_tax = required_arg(argc, argv, "--ox-taxonomy");
    const auto fasta_path = required_arg(argc, argv, "--fasta");
    const auto output = required_arg(argc, argv, "--output");
    const auto summary = required_arg(argc, argv, "--summary");
    const auto release = required_arg(argc, argv, "--release-id");
    if (argc != 15 || release.empty()) {
        throw std::runtime_error("expected seven nonempty named arguments");
    }
    for (int i = 1; i < argc; i += 2) {
        const std::string arg(argv[i]);
        if (arg != "--targets" && arg != "--target-taxonomy" &&
            arg != "--ox-taxonomy" && arg != "--fasta" &&
            arg != "--output" && arg != "--summary" &&
            arg != "--release-id") {
            throw std::runtime_error("unsupported argument: " + arg);
        }
    }
    if (output == summary) throw std::runtime_error("output paths coincide");
    if (std::filesystem::exists(output) || std::filesystem::is_symlink(output)) {
        throw std::runtime_error("output already exists");
    }
    if (std::filesystem::exists(summary) || std::filesystem::is_symlink(summary)) {
        throw std::runtime_error("summary already exists");
    }
    auto index = targets(seq_path, target_tax);
    const auto taxonomy = ox_taxonomy(candidate_tax);
    std::ifstream fasta(fasta_path);
    if (!fasta) throw std::runtime_error("cannot open FULL FASTA");
    std::ofstream pairs(output);
    if (!pairs) throw std::runtime_error("cannot open output file");
    pairs << "release\ttarget_id\trecord_number\trid\taccession\t"
             "raw_ox\ttarget_resolved_taxid\tcandidate_resolved_taxid\t"
             "species_taxid\tfrag\tlength_aa\texact_full_sequence\t"
             "discovery_channel\n";
    Counts counts;
    std::string header;
    std::string sequence;
    std::string line;
    auto process = [&]() {
        if (header.empty()) return;
        ++counts.records;
        if (!header.starts_with("sp|") && !header.starts_with("tr|")) {
            throw std::runtime_error("unexpected enriched FASTA header");
        }
        const auto rid = token(header, "RID");
        if (!decimal(rid) || std::stoull(rid) != counts.records) {
            throw std::runtime_error("FULL FASTA RID/record ordinal mismatch");
        }
        const auto frag = token(header, "FRAG");
        if (frag != "0" && frag != "1") {
            throw std::runtime_error("missing or malformed FRAG token");
        }
        if (frag == "1") {
            ++counts.fragments;
            return;
        }
        const auto raw_ox = token(header, "OX");
        if (!decimal(raw_ox)) {
            ++counts.no_ox;
            return;
        }
        const auto found_tax = taxonomy.find(raw_ox);
        if (found_tax == taxonomy.end()) {
            ++counts.unknown_ox;
            return;
        }
        const auto& tax = found_tax->second;
        if (!decimal(tax.resolved) || !decimal(tax.species)) {
            ++counts.unresolved_taxonomy;
            return;
        }
        if (!valid_sequence(sequence)) {
            ++counts.ambiguous;
            return;
        }
        const auto found = index.find(sequence);
        if (found == index.end()) return;
        std::uint64_t row_count = 0;
        const auto acc = accession(header);
        if (acc.empty()) throw std::runtime_error("missing UniProt accession");
        for (const auto& t : found->second) {
            if (t.species != tax.species || t.taxid == tax.resolved) continue;
            pairs << release << '\t' << t.id << '\t' << counts.records
                  << '\t' << rid << '\t' << acc << '\t' << raw_ox << '\t'
                  << t.taxid << '\t' << tax.resolved << '\t' << tax.species
                  << "\t0\t" << sequence.size() << "\t1\tFULL_V2_EXACT\n";
            ++row_count;
        }
        counts.matches += row_count;
        if (row_count != 0) ++counts.matching_records;
    };
    while (std::getline(fasta, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty() && line[0] == '>') {
            process();
            header = line.substr(1);
            sequence.clear();
        } else {
            if (header.empty()) throw std::runtime_error("sequence before header");
            sequence += line;
        }
    }
    process();
    if (!fasta.eof()) throw std::runtime_error("FULL FASTA read failure");
    pairs.flush();
    if (!pairs) throw std::runtime_error("cannot complete output TSV");
    pairs.close();
    std::ofstream report(summary);
    if (!report) throw std::runtime_error("cannot create summary TSV");
    report << "metric\tvalue\n";
    report << "release\t" << release << '\n';
    report << "targets_indexed_sequences\t" << index.size() << '\n';
    report << "full_fasta_records\t" << counts.records << '\n';
    report << "records_missing_or_invalid_ox\t" << counts.no_ox << '\n';
    report << "records_unknown_ox\t" << counts.unknown_ox << '\n';
    report << "records_unresolved_taxonomy\t"
           << counts.unresolved_taxonomy << '\n';
    report << "fragments_skipped\t" << counts.fragments << '\n';
    report << "ambiguous_sequences_skipped\t" << counts.ambiguous << '\n';
    report << "matched_full_records\t" << counts.matching_records << '\n';
    report << "cross_strain_exact_pairs\t" << counts.matches << '\n';
    if (!report) throw std::runtime_error("cannot write summary TSV");
}

}  // namespace

int main(int argc, char** argv) {
    try {
        scan(argc, argv);
    } catch (const std::exception& exc) {
        std::cerr << "ERROR: " << exc.what() << '\n';
        return 1;
    }
    return 0;
}
