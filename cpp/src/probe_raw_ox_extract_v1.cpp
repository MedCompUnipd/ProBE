// TESTING-only ProBE candidate-pipeline-v1
// Stage 02A: extract the distinct raw OX TaxIDs from an enriched two-line
// UniProt V2 FASTA.  No taxonomy semantics are implemented here.
//
// Output is sorted lexicographically for deterministic downstream resolution.

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

static std::string token_value(const std::string& header, const std::string& key) {
    const std::string needle = " " + key + "=";
    const auto pos = header.find(needle);
    if (pos == std::string::npos) return "";
    const auto start = pos + needle.size();
    const auto end = header.find_first_of(" \t", start);
    return header.substr(
        start,
        (end == std::string::npos ? header.size() : end) - start
    );
}

int main(int argc, char** argv) {
    try {
        std::string input_v2;
        std::string output;
        std::string stats;
        std::uint64_t progress_every = 10'000'000;

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            auto next = [&]() -> std::string {
                if (i + 1 >= argc) throw std::runtime_error("missing value for " + arg);
                return argv[++i];
            };
            if (arg == "--input-v2") input_v2 = next();
            else if (arg == "--output") output = next();
            else if (arg == "--stats") stats = next();
            else if (arg == "--progress-every") progress_every = std::stoull(next());
            else throw std::runtime_error("unknown argument: " + arg);
        }

        if (input_v2.empty() || output.empty() || stats.empty()) {
            throw std::runtime_error("required: --input-v2 --output --stats");
        }

        std::ifstream in(input_v2);
        if (!in) throw std::runtime_error("cannot open input V2 FASTA: " + input_v2);

        std::unordered_set<std::string> ox_set;
        ox_set.max_load_factor(0.80f);
        ox_set.reserve(2'000'000);

        std::uint64_t records = 0;
        std::string header, sequence;

        while (std::getline(in, header)) {
            if (!header.empty() && header.back() == '\r') header.pop_back();
            if (header.empty() || header[0] != '>') {
                throw std::runtime_error("V2 format violation: expected header line");
            }
            if (!std::getline(in, sequence)) {
                throw std::runtime_error("V2 format violation: missing sequence line");
            }
            if (!sequence.empty() && sequence.back() == '\r') sequence.pop_back();
            if (sequence.empty() || sequence[0] == '>') {
                throw std::runtime_error("V2 format violation: invalid sequence line");
            }

            const std::string ox = token_value(header, "OX");
            if (ox.empty()) {
                throw std::runtime_error("V2 header missing OX token");
            }
            ox_set.insert(ox);
            ++records;

            if (progress_every && records % progress_every == 0) {
                std::cerr << "[02A] records=" << records
                          << " distinct_OX=" << ox_set.size() << "\n";
            }
        }

        std::vector<std::string> ox_values;
        ox_values.reserve(ox_set.size());
        for (const auto& ox : ox_set) ox_values.push_back(ox);
        std::sort(ox_values.begin(), ox_values.end());

        const std::string output_tmp = output + ".tmp";
        const std::string stats_tmp = stats + ".tmp";

        {
            std::ofstream out(output_tmp);
            if (!out) throw std::runtime_error("cannot create output: " + output_tmp);
            for (const auto& ox : ox_values) out << ox << '\n';
            if (!out) throw std::runtime_error("write failure: " + output_tmp);
        }

        {
            std::ofstream out(stats_tmp);
            if (!out) throw std::runtime_error("cannot create stats: " + stats_tmp);
            out << "metric\tvalue\n";
            out << "input_v2_record_count\t" << records << '\n';
            out << "distinct_raw_ox_count\t" << ox_values.size() << '\n';
        }

        if (std::rename(output_tmp.c_str(), output.c_str()) != 0) {
            throw std::runtime_error("failed to install OX output");
        }
        if (std::rename(stats_tmp.c_str(), stats.c_str()) != 0) {
            throw std::runtime_error("failed to install stats output");
        }

        std::cerr << "[02A] COMPLETE records=" << records
                  << " distinct_OX=" << ox_values.size() << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    }
}
