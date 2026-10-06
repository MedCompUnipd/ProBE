// TESTING-only ProBE candidate-pipeline-v1
// Stage 01C: filter a complete enriched UniProt V2 FASTA to the exact set of
// accessions having at least one valid positive GO annotation from Stage 01B.
//
// Inputs:
//   --input-v2              complete two-line enriched V2 FASTA
//   --positive-accessions   one accession per line (Stage 01B output)
//   --output-v2             filtered two-line V2 FASTA
//   --stats                 TSV summary
//
// Semantics:
// - exact accession membership only;
// - preserves header and sequence content;
// - does not inspect or alter GO annotations;
// - does not remove FRAG=1 records here; fragment policy belongs downstream;
// - fails if any whitelist accession is absent from V2.
//
// No production files are modified.

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_set>

static std::string parse_primary_accession(const std::string& header) {
    if (header.empty() || header[0] != '>') {
        throw std::runtime_error("malformed V2 header: missing '>'");
    }
    const std::size_t p1 = header.find('|');
    if (p1 == std::string::npos) {
        throw std::runtime_error("malformed V2 header: first '|' missing");
    }
    const std::size_t p2 = header.find('|', p1 + 1);
    if (p2 == std::string::npos || p2 == p1 + 1) {
        throw std::runtime_error("malformed V2 header: primary accession missing");
    }
    const std::string db = header.substr(1, p1 - 1);
    if (db != "sp" && db != "tr" && db != "unk") {
        throw std::runtime_error("malformed V2 header: expected sp/tr/unk prefix");
    }
    return header.substr(p1 + 1, p2 - p1 - 1);
}

int main(int argc, char** argv) {
    try {
        std::string input_v2;
        std::string positive_path;
        std::string output_v2;
        std::string stats_path;
        std::uint64_t progress_every = 10000000;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) {
                    throw std::runtime_error("missing value for " + arg);
                }
                return argv[++i];
            };

            if (arg == "--input-v2") {
                input_v2 = next();
            } else if (arg == "--positive-accessions") {
                positive_path = next();
            } else if (arg == "--output-v2") {
                output_v2 = next();
            } else if (arg == "--stats") {
                stats_path = next();
            } else if (arg == "--progress-every") {
                progress_every = std::stoull(next());
            } else {
                throw std::runtime_error("unknown argument: " + arg);
            }
        }

        if (input_v2.empty() || positive_path.empty() ||
            output_v2.empty() || stats_path.empty()) {
            throw std::runtime_error(
                "required: --input-v2 --positive-accessions --output-v2 --stats"
            );
        }

        std::ifstream positive_in(positive_path);
        if (!positive_in) {
            throw std::runtime_error(
                "cannot open positive accession list: " + positive_path
            );
        }

        std::string line;
        std::uint64_t whitelist_count = 0;
        while (std::getline(positive_in, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) {
                throw std::runtime_error("blank line in positive accession list");
            }
            ++whitelist_count;
        }

        positive_in.clear();
        positive_in.seekg(0);

        std::unordered_set<std::string> wanted;
        wanted.max_load_factor(0.80f);
        wanted.reserve(static_cast<std::size_t>(whitelist_count));

        std::uint64_t loaded_count = 0;
        while (std::getline(positive_in, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            const auto inserted = wanted.insert(line);
            if (!inserted.second) {
                throw std::runtime_error(
                    "duplicate accession in positive whitelist: " + line
                );
            }
            ++loaded_count;
        }

        if (loaded_count != whitelist_count) {
            throw std::runtime_error("internal whitelist count mismatch");
        }

        std::cerr << "[01C] positive whitelist loaded: "
                  << whitelist_count << "\n";

        std::ifstream in(input_v2);
        if (!in) {
            throw std::runtime_error("cannot open input V2 FASTA: " + input_v2);
        }

        const std::string output_tmp = output_v2 + ".tmp";
        const std::string stats_tmp = stats_path + ".tmp";

        std::ofstream out(output_tmp, std::ios::binary);
        if (!out) {
            throw std::runtime_error("cannot create output: " + output_tmp);
        }

        std::uint64_t input_records = 0;
        std::uint64_t output_records = 0;
        std::uint64_t selected_sequence_residues = 0;

        std::string header;
        std::string sequence;

        while (std::getline(in, header)) {
            if (!header.empty() && header.back() == '\r') header.pop_back();
            if (header.empty() || header[0] != '>') {
                throw std::runtime_error(
                    "V2 format violation: expected non-empty header line"
                );
            }

            if (!std::getline(in, sequence)) {
                throw std::runtime_error(
                    "V2 format violation: missing sequence line at EOF"
                );
            }
            if (!sequence.empty() && sequence.back() == '\r') sequence.pop_back();
            if (sequence.empty() || sequence[0] == '>') {
                throw std::runtime_error(
                    "V2 format violation: expected one non-empty sequence line"
                );
            }

            const std::string accession = parse_primary_accession(header);
            ++input_records;

            auto it = wanted.find(accession);
            if (it != wanted.end()) {
                out.write(header.data(), static_cast<std::streamsize>(header.size()));
                out.put('\n');
                out.write(sequence.data(), static_cast<std::streamsize>(sequence.size()));
                out.put('\n');

                ++output_records;
                selected_sequence_residues += sequence.size();
                wanted.erase(it);
            }

            if (progress_every != 0 && input_records % progress_every == 0) {
                std::cerr << "[01C] input_records=" << input_records
                          << " output_records=" << output_records
                          << " whitelist_remaining=" << wanted.size()
                          << "\n";
            }
        }

        out.flush();
        if (!out) {
            throw std::runtime_error("write failure for output V2 FASTA");
        }
        out.close();

        if (!wanted.empty()) {
            std::cerr << "[01C] ERROR: whitelist accessions absent from input V2: "
                      << wanted.size() << "\n";
            std::size_t shown = 0;
            for (const auto& acc : wanted) {
                std::cerr << "[01C] missing accession: " << acc << "\n";
                if (++shown >= 20) break;
            }
            throw std::runtime_error(
                "positive whitelist contains accessions absent from V2"
            );
        }

        if (output_records != whitelist_count) {
            throw std::runtime_error(
                "output record count differs from positive whitelist count"
            );
        }

        std::ofstream stats(stats_tmp);
        if (!stats) {
            throw std::runtime_error("cannot create stats: " + stats_tmp);
        }
        stats << "metric\tvalue\n"
              << "positive_whitelist_count\t" << whitelist_count << "\n"
              << "input_v2_record_count\t" << input_records << "\n"
              << "output_v2_record_count\t" << output_records << "\n"
              << "selected_sequence_residue_count\t"
              << selected_sequence_residues << "\n"
              << "whitelist_missing_from_v2\t0\n";
        stats.close();

        if (std::rename(output_tmp.c_str(), output_v2.c_str()) != 0) {
            throw std::runtime_error("failed to atomically install output V2");
        }
        if (std::rename(stats_tmp.c_str(), stats_path.c_str()) != 0) {
            throw std::runtime_error("failed to atomically install stats");
        }

        std::cerr << "[01C] COMPLETE input_records=" << input_records
                  << " output_records=" << output_records
                  << " residues=" << selected_sequence_residues
                  << "\n";

        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    }
}
