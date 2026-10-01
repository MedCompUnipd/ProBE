#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

#include <zlib.h>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

static constexpr int FORMAT_VERSION = 2;
static constexpr size_t IO_BUFFER_SIZE = 1u << 20;
static constexpr size_t GZ_BUFFER_SIZE = 8u << 20;

struct GeneGroup {
    std::vector<std::string> names;
    std::vector<std::string> synonyms;
    std::vector<std::string> ordered_locus_names;
    std::vector<std::string> orf_names;
};

struct RecordState {
    std::string entry_name;
    std::string section = "unk";
    std::vector<std::string> accessions;
    std::vector<std::string> taxids;
    std::vector<std::string> de_flags;
    std::vector<std::string> gn_lines;
    std::string pe;
    std::string sequence_version;
    std::optional<uint64_t> declared_sequence_length;
    std::vector<std::string> sequence_parts;
    bool in_sequence = false;
    uint64_t start_line = 0;
};

struct SourceStats {
    uint64_t source_index = 0;
    std::string path;
    uint64_t records_seen = 0;
    uint64_t records_written = 0;
    uint64_t records_skipped = 0;
    uint64_t reviewed_records = 0;
    uint64_t unreviewed_records = 0;
    uint64_t unknown_section_records = 0;
    uint64_t fragment_records = 0;
    uint64_t non_fragment_records = 0;
    uint64_t missing_taxid_records = 0;
    uint64_t multiple_taxid_records = 0;
    uint64_t length_mismatch_records = 0;
    uint64_t records_with_secondary_accessions = 0;
    uint64_t records_with_gene_metadata = 0;
    uint64_t records_with_multiple_gene_groups = 0;
    std::map<std::string, uint64_t> flag_combinations;
};

struct Totals {
    uint64_t records_seen = 0;
    uint64_t records_written = 0;
    uint64_t records_skipped = 0;
    uint64_t reviewed_records = 0;
    uint64_t unreviewed_records = 0;
    uint64_t unknown_section_records = 0;
    uint64_t fragment_records = 0;
    uint64_t non_fragment_records = 0;
    uint64_t missing_taxid_records = 0;
    uint64_t multiple_taxid_records = 0;
    uint64_t length_mismatch_records = 0;
    uint64_t records_with_secondary_accessions = 0;
    uint64_t records_with_gene_metadata = 0;
    uint64_t records_with_multiple_gene_groups = 0;
    std::map<std::string, uint64_t> flag_combinations;
};

static bool ascii_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

static std::string trim(std::string_view s) {
    size_t a = 0;
    while (a < s.size() && ascii_space(static_cast<unsigned char>(s[a]))) ++a;
    size_t b = s.size();
    while (b > a && ascii_space(static_cast<unsigned char>(s[b - 1]))) --b;
    return std::string(s.substr(a, b - a));
}

static std::string lower_ascii(std::string_view s) {
    std::string out;
    out.reserve(s.size());
    for (unsigned char c : s) out.push_back(static_cast<char>(std::tolower(c)));
    return out;
}

static std::string clean_text(std::string_view input) {
    if (input.empty()) return {};

    std::string no_eco;
    no_eco.reserve(input.size());
    size_t pos = 0;
    while (pos < input.size()) {
        size_t eco = input.find("{ECO:", pos);
        if (eco == std::string_view::npos) {
            no_eco.append(input.substr(pos));
            break;
        }
        size_t close = input.find('}', eco + 5);
        if (close == std::string_view::npos) {
            no_eco.append(input.substr(pos));
            break;
        }
        no_eco.append(input.substr(pos, eco - pos));
        while (!no_eco.empty() && ascii_space(static_cast<unsigned char>(no_eco.back()))) {
            no_eco.pop_back();
        }
        pos = close + 1;
    }

    std::string out;
    out.reserve(no_eco.size());
    bool in_ws = true;
    for (unsigned char c : no_eco) {
        if (ascii_space(c)) {
            if (!in_ws) {
                out.push_back(' ');
                in_ws = true;
            }
        } else {
            out.push_back(static_cast<char>(c));
            in_ws = false;
        }
    }
    if (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

static void unique_preserving_order(std::vector<std::string>& values) {
    std::vector<std::string> result;
    result.reserve(values.size());
    std::unordered_set<std::string> seen;
    for (auto& raw : values) {
        std::string value = trim(raw);
        if (!value.empty() && seen.insert(value).second) result.push_back(std::move(value));
    }
    values.swap(result);
}

static bool unreserved(unsigned char c) {
    return (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z') ||
           (c >= '0' && c <= '9') ||
           c == '.' || c == '_' || c == '-' || c == '~';
}

static std::string encode_value(std::string_view value) {
    static constexpr char HEX[] = "0123456789ABCDEF";
    std::string out;
    out.reserve(value.size());
    for (unsigned char c : value) {
        if (unreserved(c)) {
            out.push_back(static_cast<char>(c));
        } else {
            out.push_back('%');
            out.push_back(HEX[(c >> 4) & 0xF]);
            out.push_back(HEX[c & 0xF]);
        }
    }
    return out;
}

static std::string encode_list(const std::vector<std::string>& values) {
    std::string out;
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) out.push_back(',');
        out += encode_value(values[i]);
    }
    return out;
}

class LineReader {
public:
    explicit LineReader(const fs::path& path) : path_(path), gz_(path.extension() == ".gz") {
        if (gz_) {
            gzfile_ = gzopen(path.string().c_str(), "rb");
            if (!gzfile_) throw std::runtime_error("cannot open gzip input: " + path.string());
            gzbuffer(gzfile_, static_cast<unsigned int>(GZ_BUFFER_SIZE));
            buffer_.resize(GZ_BUFFER_SIZE);
        } else {
            input_.open(path, std::ios::binary);
            if (!input_) throw std::runtime_error("cannot open input: " + path.string());
            input_.rdbuf()->pubsetbuf(file_buffer_.data(), file_buffer_.size());
        }
    }

    ~LineReader() {
        if (gzfile_) gzclose(gzfile_);
    }

    bool getline(std::string& line) {
        if (!gz_) {
            if (!std::getline(input_, line)) return false;
            if (!line.empty() && line.back() == '\r') line.pop_back();
            return true;
        }

        line.clear();
        while (true) {
            if (pos_ < end_) {
                const char* begin = buffer_.data() + pos_;
                const char* finish = buffer_.data() + end_;
                const char* nl = std::find(begin, finish, '\n');
                if (nl != finish) {
                    line.append(begin, static_cast<size_t>(nl - begin));
                    pos_ += static_cast<size_t>(nl - begin) + 1;
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    return true;
                }
                line.append(begin, static_cast<size_t>(finish - begin));
                pos_ = end_;
            }

            int n = gzread(gzfile_, buffer_.data(), static_cast<unsigned int>(buffer_.size()));
            if (n < 0) {
                int errnum = 0;
                const char* msg = gzerror(gzfile_, &errnum);
                throw std::runtime_error("gzip read error in " + path_.string() + ": " + (msg ? msg : "unknown"));
            }
            if (n == 0) {
                if (!line.empty()) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    return true;
                }
                return false;
            }
            pos_ = 0;
            end_ = static_cast<size_t>(n);
        }
    }

private:
    fs::path path_;
    bool gz_ = false;
    gzFile gzfile_ = nullptr;
    std::ifstream input_;
    std::array<char, IO_BUFFER_SIZE> file_buffer_{};
    std::vector<char> buffer_;
    size_t pos_ = 0;
    size_t end_ = 0;
};

static std::pair<std::string, std::string> parse_section_from_id(std::string_view line) {
    std::string payload = line.size() > 5 ? trim(line.substr(5)) : std::string();
    std::string entry;
    size_t ws = payload.find_first_of(" \t\r\n\f\v");
    entry = ws == std::string::npos ? payload : payload.substr(0, ws);
    std::string section = "unk";
    if (line.find("Unreviewed;") != std::string_view::npos) section = "tr";
    else if (line.find("Reviewed;") != std::string_view::npos) section = "sp";
    return {entry, section};
}

static std::vector<std::string> parse_accession_line(std::string_view line) {
    std::vector<std::string> out;
    std::string payload = line.size() > 5 ? trim(line.substr(5)) : std::string();
    size_t start = 0;
    while (start <= payload.size()) {
        size_t semi = payload.find(';', start);
        size_t end = semi == std::string::npos ? payload.size() : semi;
        std::string v = trim(std::string_view(payload).substr(start, end - start));
        if (!v.empty()) out.push_back(std::move(v));
        if (semi == std::string::npos) break;
        start = semi + 1;
    }
    return out;
}

static std::vector<std::string> parse_taxids(std::string_view line) {
    std::vector<std::string> out;
    static constexpr std::string_view key = "NCBI_TaxID=";
    size_t pos = 0;
    while ((pos = line.find(key, pos)) != std::string_view::npos) {
        pos += key.size();
        size_t begin = pos;
        while (pos < line.size() && line[pos] >= '0' && line[pos] <= '9') ++pos;
        if (pos > begin) out.emplace_back(line.substr(begin, pos - begin));
    }
    return out;
}

static std::vector<std::string> parse_de_flags(std::string_view line) {
    static constexpr std::string_view prefix = "DE   Flags:";
    if (line.substr(0, std::min(line.size(), prefix.size())) != prefix) return {};
    std::string payload = trim(line.substr(prefix.size()));
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= payload.size()) {
        size_t semi = payload.find(';', start);
        size_t end = semi == std::string::npos ? payload.size() : semi;
        std::string v = clean_text(std::string_view(payload).substr(start, end - start));
        if (!v.empty()) out.push_back(std::move(v));
        if (semi == std::string::npos) break;
        start = semi + 1;
    }
    return out;
}

static bool is_strict_fragment(const std::vector<std::string>& flags) {
    for (const auto& flag : flags) {
        std::string x = lower_ascii(flag);
        if (x == "fragment" || x == "fragments") return true;
    }
    return false;
}

static std::string parse_pe(std::string_view line) {
    std::string payload = line.size() > 5 ? trim(line.substr(5)) : std::string();
    size_t p = 0;
    while (p < payload.size() && payload[p] >= '0' && payload[p] <= '9') ++p;
    if (p == 0) return {};
    size_t q = p;
    while (q < payload.size() && ascii_space(static_cast<unsigned char>(payload[q]))) ++q;
    if (q < payload.size() && payload[q] == ':') return payload.substr(0, p);
    return {};
}

static std::string parse_sequence_version(std::string_view line) {
    std::string lower = lower_ascii(line);
    static constexpr std::string_view key = "sequence version";
    size_t pos = lower.find(key);
    if (pos == std::string::npos) return {};
    pos += key.size();
    while (pos < lower.size() && ascii_space(static_cast<unsigned char>(lower[pos]))) ++pos;
    size_t begin = pos;
    while (pos < lower.size() && lower[pos] >= '0' && lower[pos] <= '9') ++pos;
    if (pos == begin) return {};
    return std::string(line.substr(begin, pos - begin));
}

static std::optional<uint64_t> parse_declared_sequence_length(std::string_view line) {
    std::string upper(line);
    for (char& c : upper) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    static constexpr std::string_view key = "SEQUENCE";
    size_t pos = upper.find(key);
    if (pos == std::string::npos) return std::nullopt;
    pos += key.size();
    while (pos < upper.size() && ascii_space(static_cast<unsigned char>(upper[pos]))) ++pos;
    size_t begin = pos;
    while (pos < upper.size() && upper[pos] >= '0' && upper[pos] <= '9') ++pos;
    if (pos == begin) return std::nullopt;
    while (pos < upper.size() && ascii_space(static_cast<unsigned char>(upper[pos]))) ++pos;
    if (pos + 2 > upper.size() || upper.substr(pos, 2) != "AA") return std::nullopt;
    return static_cast<uint64_t>(std::stoull(std::string(upper.substr(begin, pos - begin))));
}

static std::string normalize_sequence(const std::vector<std::string>& parts) {
    size_t total = 0;
    for (const auto& p : parts) total += p.size();
    std::string out;
    out.reserve(total);
    for (const auto& part : parts) {
        for (unsigned char c : part) {
            if (!ascii_space(c)) out.push_back(static_cast<char>(std::toupper(c)));
        }
    }
    return out;
}

static std::vector<std::vector<std::string>> split_gene_groups(const std::vector<std::string>& gn_lines) {
    std::vector<std::vector<std::string>> groups;
    std::vector<std::string> current;
    for (const auto& raw : gn_lines) {
        std::string payload = clean_text(raw);
        if (payload.empty()) continue;
        if (lower_ascii(payload) == "and") {
            if (!current.empty()) {
                groups.push_back(std::move(current));
                current.clear();
            }
            continue;
        }
        current.push_back(std::move(payload));
    }
    if (!current.empty()) groups.push_back(std::move(current));
    return groups;
}

static void append_values(std::vector<std::string>& dest, std::string_view raw_values) {
    std::vector<std::string> values;
    size_t start = 0;
    while (start <= raw_values.size()) {
        size_t comma = raw_values.find(',', start);
        size_t end = comma == std::string_view::npos ? raw_values.size() : comma;
        std::string v = clean_text(raw_values.substr(start, end - start));
        if (!v.empty()) values.push_back(std::move(v));
        if (comma == std::string_view::npos) break;
        start = comma + 1;
    }
    unique_preserving_order(values);
    dest.insert(dest.end(), values.begin(), values.end());
}

static std::vector<GeneGroup> parse_gene_groups(const std::vector<std::string>& gn_lines) {
    std::vector<GeneGroup> result;
    auto groups = split_gene_groups(gn_lines);
    static const std::array<std::pair<std::string_view, int>, 4> keys{{
        {"Name=", 0}, {"Synonyms=", 1}, {"OrderedLocusNames=", 2}, {"ORFNames=", 3}
    }};

    for (const auto& raw_group : groups) {
        std::string joined;
        for (size_t i = 0; i < raw_group.size(); ++i) {
            if (i) joined.push_back(' ');
            joined += raw_group[i];
        }
        std::string text = clean_text(joined);
        GeneGroup group;

        for (size_t i = 0; i < text.size(); ++i) {
            bool boundary = (i == 0) || text[i - 1] == ';' || ascii_space(static_cast<unsigned char>(text[i - 1]));
            if (!boundary) continue;
            for (const auto& [key, kind] : keys) {
                if (i + key.size() > text.size()) continue;
                if (std::string_view(text).substr(i, key.size()) != key) continue;
                size_t value_start = i + key.size();
                size_t value_end = text.find(';', value_start);
                if (value_end == std::string::npos) value_end = text.size();
                std::string_view raw_values(text.data() + value_start, value_end - value_start);
                if (kind == 0) append_values(group.names, raw_values);
                else if (kind == 1) append_values(group.synonyms, raw_values);
                else if (kind == 2) append_values(group.ordered_locus_names, raw_values);
                else append_values(group.orf_names, raw_values);
                break;
            }
        }

        unique_preserving_order(group.names);
        unique_preserving_order(group.synonyms);
        unique_preserving_order(group.ordered_locus_names);
        unique_preserving_order(group.orf_names);
        result.push_back(std::move(group));
    }
    return result;
}

static std::string encode_gene_group(const GeneGroup& group) {
    std::vector<std::string> fields;
    if (!group.names.empty()) fields.push_back("N:" + encode_list(group.names));
    if (!group.synonyms.empty()) fields.push_back("S:" + encode_list(group.synonyms));
    if (!group.ordered_locus_names.empty()) fields.push_back("L:" + encode_list(group.ordered_locus_names));
    if (!group.orf_names.empty()) fields.push_back("O:" + encode_list(group.orf_names));
    std::string out;
    for (size_t i = 0; i < fields.size(); ++i) {
        if (i) out.push_back(';');
        out += fields[i];
    }
    return out;
}

static std::string encode_gene_groups(const std::vector<GeneGroup>& groups) {
    std::string out;
    bool first = true;
    for (const auto& group : groups) {
        std::string encoded = encode_gene_group(group);
        if (encoded.empty()) continue;
        if (!first) out.push_back('|');
        out += encoded;
        first = false;
    }
    return out;
}

static std::string join_raw(const std::vector<std::string>& values, char sep) {
    std::string out;
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) out.push_back(sep);
        out += values[i];
    }
    return out;
}

static std::string build_fasta_header(
    uint64_t rid,
    uint64_t source_index,
    uint64_t source_record,
    const RecordState& state,
    uint64_t sequence_length,
    const std::vector<GeneGroup>& gene_groups
) {
    const std::string& primary = state.accessions.front();
    std::vector<std::string> secondary(state.accessions.begin() + 1, state.accessions.end());
    std::vector<std::string> flags = state.de_flags;
    unique_preserving_order(flags);
    bool strict_fragment = is_strict_fragment(flags);
    std::string taxid_value = state.taxids.empty() ? "NA" : join_raw(state.taxids, ',');

    std::vector<std::string> tokens;
    tokens.reserve(14);
    tokens.push_back(">" + state.section + "|" + primary + "|" + state.entry_name);
    tokens.push_back("RID=" + std::to_string(rid));
    tokens.push_back("SRC=" + std::to_string(source_index));
    tokens.push_back("REC=" + std::to_string(source_record));
    tokens.push_back("LINE=" + std::to_string(state.start_line));
    tokens.push_back("OX=" + taxid_value);
    tokens.push_back(std::string("FRAG=") + (strict_fragment ? "1" : "0"));
    tokens.push_back("LEN=" + std::to_string(sequence_length));
    tokens.push_back("PE=" + (state.pe.empty() ? "NA" : state.pe));
    tokens.push_back("SV=" + (state.sequence_version.empty() ? "NA" : state.sequence_version));
    if (!flags.empty()) tokens.push_back("FLAGS=" + encode_list(flags));
    if (!secondary.empty()) tokens.push_back("SEC=" + encode_list(secondary));
    std::string genes = encode_gene_groups(gene_groups);
    if (!genes.empty()) tokens.push_back("GENE=" + genes);

    std::string out;
    for (size_t i = 0; i < tokens.size(); ++i) {
        if (i) out.push_back(' ');
        out += tokens[i];
    }
    return out;
}

static void write_issue(
    std::ostream& out,
    uint64_t source_index,
    const fs::path& source_path,
    uint64_t source_record,
    uint64_t source_line,
    std::optional<uint64_t> rid,
    std::string issue,
    std::string detail,
    const std::string& accession = {},
    const std::string& entry_name = {}
) {
    std::replace(detail.begin(), detail.end(), '\t', ' ');
    std::replace(detail.begin(), detail.end(), '\n', ' ');
    out << source_index << '\t'
        << source_path.string() << '\t'
        << source_record << '\t'
        << source_line << '\t';
    if (rid) out << *rid;
    out << '\t' << issue << '\t' << detail << '\t' << accession << '\t' << entry_name << '\n';
}

static std::pair<uint64_t, bool> finalize_record(
    RecordState& state,
    uint64_t source_index,
    const fs::path& source_path,
    uint64_t source_record,
    uint64_t rid,
    std::ostream& fasta_out,
    std::ostream& issues_out,
    SourceStats& stats
) {
    stats.records_seen++;
    unique_preserving_order(state.accessions);
    unique_preserving_order(state.taxids);
    unique_preserving_order(state.de_flags);

    std::string primary = state.accessions.empty() ? "" : state.accessions.front();
    std::vector<std::string> core_errors;
    if (state.entry_name.empty()) core_errors.push_back("missing ID/entry name");
    if (primary.empty()) core_errors.push_back("missing primary accession");
    if (state.sequence_parts.empty()) core_errors.push_back("missing sequence");

    if (!core_errors.empty()) {
        stats.records_skipped++;
        std::string detail;
        for (size_t i = 0; i < core_errors.size(); ++i) {
            if (i) detail += "; ";
            detail += core_errors[i];
        }
        write_issue(issues_out, source_index, source_path, source_record, state.start_line,
                    std::nullopt, "RECORD_SKIPPED", detail, primary, state.entry_name);
        return {rid, false};
    }

    std::string sequence = normalize_sequence(state.sequence_parts);
    uint64_t sequence_length = sequence.size();

    if (state.taxids.empty()) {
        stats.missing_taxid_records++;
        write_issue(issues_out, source_index, source_path, source_record, state.start_line,
                    rid, "MISSING_TAXID", "No NCBI_TaxID found in OX", primary, state.entry_name);
    } else if (state.taxids.size() > 1) {
        stats.multiple_taxid_records++;
        write_issue(issues_out, source_index, source_path, source_record, state.start_line,
                    rid, "MULTIPLE_TAXIDS", join_raw(state.taxids, ','), primary, state.entry_name);
    }

    if (state.declared_sequence_length && *state.declared_sequence_length != sequence_length) {
        stats.length_mismatch_records++;
        write_issue(issues_out, source_index, source_path, source_record, state.start_line,
                    rid, "SEQUENCE_LENGTH_MISMATCH",
                    "SQ=" + std::to_string(*state.declared_sequence_length) + ";parsed=" + std::to_string(sequence_length),
                    primary, state.entry_name);
    }

    if (state.section == "sp") stats.reviewed_records++;
    else if (state.section == "tr") stats.unreviewed_records++;
    else stats.unknown_section_records++;

    bool strict_fragment = is_strict_fragment(state.de_flags);
    if (strict_fragment) stats.fragment_records++;
    else stats.non_fragment_records++;

    std::string flag_key = state.de_flags.empty() ? "NONE" : join_raw(state.de_flags, '|');
    stats.flag_combinations[flag_key]++;
    if (state.accessions.size() > 1) stats.records_with_secondary_accessions++;

    auto gene_groups = parse_gene_groups(state.gn_lines);
    if (!gene_groups.empty()) stats.records_with_gene_metadata++;
    if (gene_groups.size() > 1) stats.records_with_multiple_gene_groups++;

    std::string header = build_fasta_header(rid, source_index, source_record, state, sequence_length, gene_groups);
    fasta_out << header << '\n' << sequence << '\n';
    stats.records_written++;
    return {rid + 1, true};
}

static std::pair<uint64_t, SourceStats> process_source(
    uint64_t source_index,
    const fs::path& source_path,
    uint64_t starting_rid,
    std::ostream& fasta_out,
    std::ostream& issues_out,
    uint64_t progress_every
) {
    SourceStats stats;
    stats.source_index = source_index;
    stats.path = source_path.string();
    RecordState state;
    uint64_t rid = starting_rid;
    uint64_t source_record = 0;
    uint64_t line_number = 0;
    auto started = Clock::now();

    std::cerr << "[source " << source_index << "] START " << source_path.string() << std::endl;
    LineReader reader(source_path);
    std::string line;
    while (reader.getline(line)) {
        ++line_number;
        std::string_view code = line.size() >= 2 ? std::string_view(line).substr(0, 2) : std::string_view(line);
        if (code == "ID") {
            auto parsed = parse_section_from_id(line);
            state.entry_name = std::move(parsed.first);
            state.section = std::move(parsed.second);
            state.start_line = line_number;
        } else if (code == "AC") {
            auto values = parse_accession_line(line);
            state.accessions.insert(state.accessions.end(), values.begin(), values.end());
        } else if (code == "DE") {
            auto values = parse_de_flags(line);
            state.de_flags.insert(state.de_flags.end(), values.begin(), values.end());
        } else if (code == "OX") {
            auto values = parse_taxids(line);
            state.taxids.insert(state.taxids.end(), values.begin(), values.end());
        } else if (code == "GN") {
            state.gn_lines.push_back(line.size() > 5 ? trim(std::string_view(line).substr(5)) : std::string());
        } else if (code == "PE") {
            std::string pe = parse_pe(line);
            if (!pe.empty()) state.pe = std::move(pe);
        } else if (code == "DT") {
            std::string sv = parse_sequence_version(line);
            if (!sv.empty()) state.sequence_version = std::move(sv);
        } else if (code == "SQ") {
            state.in_sequence = true;
            state.declared_sequence_length = parse_declared_sequence_length(line);
        } else if (code == "//") {
            source_record++;
            auto result = finalize_record(state, source_index, source_path, source_record, rid,
                                          fasta_out, issues_out, stats);
            rid = result.first;
            if (progress_every > 0 && source_record % progress_every == 0) {
                double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
                double rate = elapsed > 0 ? static_cast<double>(source_record) / elapsed : 0.0;
                std::cerr << "[source " << source_index << "] records=" << source_record
                          << " written=" << stats.records_written
                          << " fragments=" << stats.fragment_records
                          << " rate=" << std::fixed << std::setprecision(0) << rate << " rec/s" << std::endl;
            }
            state = RecordState{};
        } else if (state.in_sequence) {
            std::string seq;
            seq.reserve(line.size());
            for (unsigned char c : line) if (!ascii_space(c)) seq.push_back(static_cast<char>(c));
            if (!seq.empty()) state.sequence_parts.push_back(std::move(seq));
        }
    }

    if (!state.entry_name.empty() || !state.accessions.empty() || !state.sequence_parts.empty()) {
        write_issue(issues_out, source_index, source_path, source_record + 1, state.start_line,
                    std::nullopt, "UNTERMINATED_RECORD", "EOF reached before //",
                    state.accessions.empty() ? "" : state.accessions.front(), state.entry_name);
        stats.records_skipped++;
    }

    double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
    std::cerr << "[source " << source_index << "] END records=" << stats.records_seen
              << " written=" << stats.records_written
              << " fragments=" << stats.fragment_records
              << " elapsed=" << std::fixed << std::setprecision(1) << elapsed << "s" << std::endl;
    return {rid, std::move(stats)};
}

static std::string json_escape(std::string_view s) {
    std::ostringstream out;
    for (unsigned char c : s) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c)
                        << std::dec << std::setfill(' ');
                } else {
                    out << static_cast<char>(c);
                }
        }
    }
    return out.str();
}

static void add_stats(Totals& t, const SourceStats& s) {
    t.records_seen += s.records_seen;
    t.records_written += s.records_written;
    t.records_skipped += s.records_skipped;
    t.reviewed_records += s.reviewed_records;
    t.unreviewed_records += s.unreviewed_records;
    t.unknown_section_records += s.unknown_section_records;
    t.fragment_records += s.fragment_records;
    t.non_fragment_records += s.non_fragment_records;
    t.missing_taxid_records += s.missing_taxid_records;
    t.multiple_taxid_records += s.multiple_taxid_records;
    t.length_mismatch_records += s.length_mismatch_records;
    t.records_with_secondary_accessions += s.records_with_secondary_accessions;
    t.records_with_gene_metadata += s.records_with_gene_metadata;
    t.records_with_multiple_gene_groups += s.records_with_multiple_gene_groups;
    for (const auto& [k, v] : s.flag_combinations) t.flag_combinations[k] += v;
}

static void write_flag_map(std::ostream& out, const std::map<std::string, uint64_t>& flags, int indent) {
    out << "{\n";
    size_t i = 0;
    for (const auto& [k, v] : flags) {
        out << std::string(indent + 2, ' ') << '"' << json_escape(k) << "\": " << v;
        if (++i < flags.size()) out << ',';
        out << '\n';
    }
    out << std::string(indent, ' ') << '}';
}

static void write_source_stats_json(std::ostream& out, const SourceStats& s, int indent) {
    const std::string p(indent, ' ');
    out << p << "{\n";
    out << p << "  \"flag_combinations\": "; write_flag_map(out, s.flag_combinations, indent + 2); out << ",\n";
    out << p << "  \"fragment_records\": " << s.fragment_records << ",\n";
    out << p << "  \"length_mismatch_records\": " << s.length_mismatch_records << ",\n";
    out << p << "  \"missing_taxid_records\": " << s.missing_taxid_records << ",\n";
    out << p << "  \"multiple_taxid_records\": " << s.multiple_taxid_records << ",\n";
    out << p << "  \"non_fragment_records\": " << s.non_fragment_records << ",\n";
    out << p << "  \"path\": \"" << json_escape(s.path) << "\",\n";
    out << p << "  \"records_skipped\": " << s.records_skipped << ",\n";
    out << p << "  \"records_seen\": " << s.records_seen << ",\n";
    out << p << "  \"records_with_gene_metadata\": " << s.records_with_gene_metadata << ",\n";
    out << p << "  \"records_with_multiple_gene_groups\": " << s.records_with_multiple_gene_groups << ",\n";
    out << p << "  \"records_with_secondary_accessions\": " << s.records_with_secondary_accessions << ",\n";
    out << p << "  \"records_written\": " << s.records_written << ",\n";
    out << p << "  \"reviewed_records\": " << s.reviewed_records << ",\n";
    out << p << "  \"source_index\": " << s.source_index << ",\n";
    out << p << "  \"unknown_section_records\": " << s.unknown_section_records << ",\n";
    out << p << "  \"unreviewed_records\": " << s.unreviewed_records << '\n';
    out << p << '}';
}

static void write_summary_json(
    const fs::path& path,
    const std::string& release_id,
    const std::vector<SourceStats>& sources,
    const Totals& t,
    double elapsed,
    const fs::path& fasta_path,
    const fs::path& issues_path,
    const fs::path& summary_path
) {
    std::ofstream out(path, std::ios::binary);
    if (!out) throw std::runtime_error("cannot write summary: " + path.string());
    out << "{\n";
    out << "  \"elapsed_seconds\": " << std::setprecision(15) << elapsed << ",\n";
    out << "  \"format_version\": " << FORMAT_VERSION << ",\n";
    out << "  \"fragment_policy\": \"STRICT_DE_FLAGS_FRAGMENT_OR_FRAGMENTS_ONLY\",\n";
    out << "  \"inputs\": [\n";
    for (size_t i = 0; i < sources.size(); ++i) {
        write_source_stats_json(out, sources[i], 4);
        if (i + 1 < sources.size()) out << ',';
        out << '\n';
    }
    out << "  ],\n";
    out << "  \"outputs\": {\n";
    out << "    \"fasta\": \"" << json_escape(fasta_path.string()) << "\",\n";
    out << "    \"issues\": \"" << json_escape(issues_path.string()) << "\",\n";
    out << "    \"summary\": \"" << json_escape(summary_path.string()) << "\"\n";
    out << "  },\n";
    out << "  \"records_per_second\": " << std::setprecision(15)
        << (elapsed > 0 ? static_cast<double>(t.records_seen) / elapsed : 0.0) << ",\n";
    out << "  \"release_id\": \"" << json_escape(release_id) << "\",\n";
    out << "  \"totals\": {\n";
    out << "    \"flag_combinations\": "; write_flag_map(out, t.flag_combinations, 4); out << ",\n";
    out << "    \"fragment_records\": " << t.fragment_records << ",\n";
    out << "    \"length_mismatch_records\": " << t.length_mismatch_records << ",\n";
    out << "    \"missing_taxid_records\": " << t.missing_taxid_records << ",\n";
    out << "    \"multiple_taxid_records\": " << t.multiple_taxid_records << ",\n";
    out << "    \"non_fragment_records\": " << t.non_fragment_records << ",\n";
    out << "    \"records_skipped\": " << t.records_skipped << ",\n";
    out << "    \"records_seen\": " << t.records_seen << ",\n";
    out << "    \"records_with_gene_metadata\": " << t.records_with_gene_metadata << ",\n";
    out << "    \"records_with_multiple_gene_groups\": " << t.records_with_multiple_gene_groups << ",\n";
    out << "    \"records_with_secondary_accessions\": " << t.records_with_secondary_accessions << ",\n";
    out << "    \"records_written\": " << t.records_written << ",\n";
    out << "    \"reviewed_records\": " << t.reviewed_records << ",\n";
    out << "    \"unknown_section_records\": " << t.unknown_section_records << ",\n";
    out << "    \"unreviewed_records\": " << t.unreviewed_records << '\n';
    out << "  }\n";
    out << "}\n";
}

struct Args {
    std::vector<fs::path> inputs;
    fs::path output_prefix;
    std::string release_id;
    uint64_t progress_every = 1000000;
    bool overwrite = false;
};

static void usage(const char* argv0) {
    std::cerr << "usage: " << argv0
              << " --input FILE [--input FILE ...] --output-prefix PREFIX --release-id ID"
              << " [--progress-every N] [--overwrite]\n";
}

static Args parse_args(int argc, char** argv) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--input") {
            if (++i >= argc) throw std::runtime_error("--input requires a value");
            args.inputs.emplace_back(argv[i]);
        } else if (a == "--output-prefix") {
            if (++i >= argc) throw std::runtime_error("--output-prefix requires a value");
            args.output_prefix = argv[i];
        } else if (a == "--release-id") {
            if (++i >= argc) throw std::runtime_error("--release-id requires a value");
            args.release_id = argv[i];
        } else if (a == "--progress-every") {
            if (++i >= argc) throw std::runtime_error("--progress-every requires a value");
            args.progress_every = std::stoull(argv[i]);
        } else if (a == "--overwrite") {
            args.overwrite = true;
        } else if (a == "-h" || a == "--help") {
            usage(argv[0]);
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + a);
        }
    }
    if (args.inputs.empty()) throw std::runtime_error("at least one --input is required");
    if (args.output_prefix.empty()) throw std::runtime_error("--output-prefix is required");
    if (args.release_id.empty()) throw std::runtime_error("--release-id is required");
    return args;
}

static void atomic_replace(const fs::path& src, const fs::path& dst) {
    if (std::rename(src.string().c_str(), dst.string().c_str()) != 0) {
        throw std::runtime_error("cannot replace " + dst.string());
    }
}

int main(int argc, char** argv) {
    try {
        Args args = parse_args(argc, argv);
        for (const auto& path : args.inputs) {
            if (!fs::is_regular_file(path)) throw std::runtime_error("Input not found: " + path.string());
        }

        fs::path fasta_path(args.output_prefix.string() + ".fasta");
        fs::path issues_path(args.output_prefix.string() + ".issues.tsv");
        fs::path summary_path(args.output_prefix.string() + ".summary.json");
        fs::path fasta_tmp(fasta_path.string() + ".tmp");
        fs::path issues_tmp(issues_path.string() + ".tmp");
        fs::path summary_tmp(summary_path.string() + ".tmp");

        if (!args.output_prefix.parent_path().empty()) fs::create_directories(args.output_prefix.parent_path());
        if (!args.overwrite) {
            std::vector<fs::path> existing;
            for (const auto& p : {fasta_path, issues_path, summary_path}) if (fs::exists(p)) existing.push_back(p);
            if (!existing.empty()) {
                std::ostringstream msg;
                msg << "Refusing to overwrite: ";
                for (size_t i = 0; i < existing.size(); ++i) {
                    if (i) msg << ", ";
                    msg << existing[i].string();
                }
                throw std::runtime_error(msg.str());
            }
        }
        for (const auto& p : {fasta_tmp, issues_tmp, summary_tmp}) {
            std::error_code ec;
            fs::remove(p, ec);
        }

        std::array<char, IO_BUFFER_SIZE> fasta_buffer{};
        std::array<char, IO_BUFFER_SIZE> issues_buffer{};
        std::ofstream fasta_out;
        std::ofstream issues_out;
        fasta_out.rdbuf()->pubsetbuf(fasta_buffer.data(), fasta_buffer.size());
        issues_out.rdbuf()->pubsetbuf(issues_buffer.data(), issues_buffer.size());
        fasta_out.open(fasta_tmp, std::ios::binary);
        issues_out.open(issues_tmp, std::ios::binary);
        if (!fasta_out) throw std::runtime_error("cannot create: " + fasta_tmp.string());
        if (!issues_out) throw std::runtime_error("cannot create: " + issues_tmp.string());

        issues_out << "source_index\tsource_path\tsource_record\tsource_line\trid\tissue\tdetail\tprimary_accession\tentry_name\n";

        auto started = Clock::now();
        std::vector<SourceStats> all_stats;
        uint64_t rid = 1;
        for (size_t i = 0; i < args.inputs.size(); ++i) {
            auto result = process_source(i, args.inputs[i], rid, fasta_out, issues_out, args.progress_every);
            rid = result.first;
            all_stats.push_back(std::move(result.second));
        }
        fasta_out.close();
        issues_out.close();
        if (!fasta_out) throw std::runtime_error("write failure: " + fasta_tmp.string());
        if (!issues_out) throw std::runtime_error("write failure: " + issues_tmp.string());

        double elapsed = std::chrono::duration<double>(Clock::now() - started).count();
        Totals totals;
        for (const auto& s : all_stats) add_stats(totals, s);
        write_summary_json(summary_tmp, args.release_id, all_stats, totals, elapsed,
                           fasta_path, issues_path, summary_path);

        atomic_replace(fasta_tmp, fasta_path);
        atomic_replace(issues_tmp, issues_path);
        atomic_replace(summary_tmp, summary_path);

        std::cerr << "\nConversion completed successfully.\n"
                  << "FASTA:   " << fasta_path.string() << '\n'
                  << "Issues:  " << issues_path.string() << '\n'
                  << "Summary: " << summary_path.string() << std::endl;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << std::endl;
        return 1;
    }
}
