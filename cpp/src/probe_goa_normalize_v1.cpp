#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr double MAX_LOAD = 0.78;

struct TransparentHash {
    using is_transparent = void;
    std::size_t operator()(std::string_view s) const noexcept {
        // FNV-1a 64-bit. Hash equality is never treated as string equality.
        std::uint64_t h = 1469598103934665603ULL;
        for (unsigned char c : s) {
            h ^= static_cast<std::uint64_t>(c);
            h *= 1099511628211ULL;
        }
        return static_cast<std::size_t>(h);
    }
    std::size_t operator()(const std::string& s) const noexcept {
        return (*this)(std::string_view(s));
    }
};

struct TransparentEqual {
    using is_transparent = void;
    bool operator()(std::string_view a, std::string_view b) const noexcept {
        return a == b;
    }
    bool operator()(const std::string& a, const std::string& b) const noexcept {
        return a == b;
    }
    bool operator()(const std::string& a, std::string_view b) const noexcept {
        return std::string_view(a) == b;
    }
    bool operator()(std::string_view a, const std::string& b) const noexcept {
        return a == std::string_view(b);
    }
};

std::uint64_t hash64(std::string_view s) noexcept {
    std::uint64_t h = 1469598103934665603ULL;
    for (unsigned char c : s) {
        h ^= static_cast<std::uint64_t>(c);
        h *= 1099511628211ULL;
    }
    return h == 0 ? 1 : h;
}

std::uint64_t parse_u64(std::string_view value, std::string_view name) {
    if (value.empty()) throw std::invalid_argument(std::string(name) + " must not be empty");
    std::uint64_t result = 0;
    for (char c : value) {
        if (c < '0' || c > '9') {
            throw std::invalid_argument(std::string(name) + " must be an unsigned integer: " + std::string(value));
        }
        const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
        if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) {
            throw std::overflow_error(std::string(name) + " is too large");
        }
        result = result * 10 + digit;
    }
    return result;
}

std::string json_escape(std::string_view s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    const char* hex = "0123456789abcdef";
                    out += "\\u00";
                    out.push_back(hex[(c >> 4) & 0xF]);
                    out.push_back(hex[c & 0xF]);
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    return out;
}

bool ascii_ci_starts_with(std::string_view value, std::string_view prefix) {
    if (value.size() < prefix.size()) return false;
    for (std::size_t i = 0; i < prefix.size(); ++i) {
        unsigned char a = static_cast<unsigned char>(value[i]);
        unsigned char b = static_cast<unsigned char>(prefix[i]);
        if (a >= 'A' && a <= 'Z') a = static_cast<unsigned char>(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z') b = static_cast<unsigned char>(b - 'A' + 'a');
        if (a != b) return false;
    }
    return true;
}

fs::path make_temp_path(const fs::path& final) {
    fs::create_directories(final.parent_path());
    const auto tick = std::chrono::high_resolution_clock::now().time_since_epoch().count();
    return final.parent_path() /
        ("." + final.filename().string() + "." + std::to_string(tick) + ".tmp");
}

void install_atomic(const fs::path& tmp, const fs::path& final) {
    std::error_code ec;
    fs::rename(tmp, final, ec);
    if (!ec) return;
    // POSIX rename replaces atomically, but std::filesystem implementations may refuse on some platforms.
    // On this Linux pipeline, remove+rename is only used when --overwrite was explicitly requested.
    fs::remove(final, ec);
    ec.clear();
    fs::rename(tmp, final, ec);
    if (ec) throw std::runtime_error("failed to install output " + final.string() + ": " + ec.message());
}

struct ScopedTemps {
    std::vector<fs::path> paths;
    bool keep = false;
    ~ScopedTemps() {
        if (keep) return;
        for (const auto& path : paths) {
            std::error_code ec;
            fs::remove(path, ec);
        }
    }
};

class ExactStringSet {
public:
    explicit ExactStringSet(std::uint64_t reserve_entries = 0) {
        if (reserve_entries) reserve(reserve_entries);
    }

    void reserve(std::uint64_t entries) {
        if (entries == 0) return;
        const long double needed_ld = static_cast<long double>(entries) / MAX_LOAD;
        if (needed_ld > static_cast<long double>(std::numeric_limits<std::size_t>::max())) {
            throw std::overflow_error("requested V2 membership table is too large");
        }
        std::size_t needed = static_cast<std::size_t>(needed_ld) + 1;
        std::size_t capacity = 1;
        while (capacity < needed) {
            if (capacity > std::numeric_limits<std::size_t>::max() / 2) {
                throw std::overflow_error("V2 membership capacity overflow");
            }
            capacity <<= 1;
        }
        rehash(capacity);
        const long double arena_hint = static_cast<long double>(entries) * 12.0L;
        if (arena_hint <= static_cast<long double>(std::numeric_limits<std::size_t>::max())) {
            arena_.reserve(static_cast<std::size_t>(arena_hint));
        }
    }

    bool insert(std::string_view value) {
        if (table_.empty()) rehash(1u << 20);
        if (static_cast<long double>(size_ + 1) > static_cast<long double>(table_.size()) * MAX_LOAD) {
            rehash(table_.size() * 2);
        }
        const std::uint64_t h = hash64(value);
        std::size_t idx = static_cast<std::size_t>(h) & (table_.size() - 1);
        for (;;) {
            Slot& slot = table_[idx];
            if (slot.hash == 0) {
                if (value.size() > std::numeric_limits<std::uint32_t>::max()) {
                    throw std::overflow_error("accession exceeds uint32 length capacity");
                }
                const std::uint64_t offset = arena_.size();
                arena_.insert(arena_.end(), value.begin(), value.end());
                slot.hash = h;
                slot.offset = offset;
                slot.length = static_cast<std::uint32_t>(value.size());
                ++size_;
                return true;
            }
            if (slot.hash == h && slot.length == value.size() && equal_slot(slot, value)) {
                return false;
            }
            idx = (idx + 1) & (table_.size() - 1);
        }
    }

    bool contains(std::string_view value) const noexcept {
        if (table_.empty()) return false;
        const std::uint64_t h = hash64(value);
        std::size_t idx = static_cast<std::size_t>(h) & (table_.size() - 1);
        for (;;) {
            const Slot& slot = table_[idx];
            if (slot.hash == 0) return false;
            if (slot.hash == h && slot.length == value.size() && equal_slot(slot, value)) {
                return true;
            }
            idx = (idx + 1) & (table_.size() - 1);
        }
    }

    std::uint64_t size() const noexcept { return size_; }
    std::uint64_t arena_bytes() const noexcept { return arena_.size(); }
    std::uint64_t capacity() const noexcept { return table_.size(); }

private:
    struct Slot {
        std::uint64_t hash = 0;
        std::uint64_t offset = 0;
        std::uint32_t length = 0;
    };

    bool equal_slot(const Slot& slot, std::string_view value) const noexcept {
        if (slot.length == 0) return value.empty();
        return std::memcmp(arena_.data() + slot.offset, value.data(), slot.length) == 0;
    }

    void rehash(std::size_t new_capacity) {
        if (new_capacity < 2) new_capacity = 2;
        if ((new_capacity & (new_capacity - 1)) != 0) {
            throw std::logic_error("ExactStringSet capacity must be a power of two");
        }
        std::vector<Slot> replacement(new_capacity);
        for (const Slot& old : table_) {
            if (old.hash == 0) continue;
            std::size_t idx = static_cast<std::size_t>(old.hash) & (new_capacity - 1);
            while (replacement[idx].hash != 0) idx = (idx + 1) & (new_capacity - 1);
            replacement[idx] = old;
        }
        table_.swap(replacement);
    }

    std::vector<char> arena_;
    std::vector<Slot> table_;
    std::uint64_t size_ = 0;
};

struct PolicyEntry {
    std::string canonical;
    std::string status;
    std::string name_space;
    bool is_root = false;
    std::string reject_reason;
};

using PolicyMap = std::unordered_map<std::string, PolicyEntry, TransparentHash, TransparentEqual>;

std::vector<std::string_view> split_tabs_all(std::string_view line) {
    std::vector<std::string_view> fields;
    std::size_t start = 0;
    for (;;) {
        const std::size_t pos = line.find('\t', start);
        if (pos == std::string_view::npos) {
            fields.emplace_back(line.substr(start));
            break;
        }
        fields.emplace_back(line.substr(start, pos - start));
        start = pos + 1;
    }
    return fields;
}

PolicyMap load_policy(const fs::path& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open policy: " + path.string());
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty policy: " + path.string());
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const std::string expected = "input_go_id\tcanonical_go_id\tresolution_status\tnamespace\tis_root\treject_reason";
    if (line != expected) throw std::runtime_error("unexpected policy header: " + line);

    PolicyMap policy;
    policy.reserve(60000);
    std::uint64_t line_number = 1;
    while (std::getline(in, line)) {
        ++line_number;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        const auto fields = split_tabs_all(line);
        if (fields.size() != 6) {
            throw std::runtime_error("policy line " + std::to_string(line_number) + ": expected 6 columns");
        }
        if (fields[0].empty()) {
            throw std::runtime_error("policy line " + std::to_string(line_number) + ": empty input_go_id");
        }
        PolicyEntry entry;
        entry.canonical = std::string(fields[1]);
        entry.status = std::string(fields[2]);
        entry.name_space = std::string(fields[3]);
        if (fields[4] == "1") entry.is_root = true;
        else if (fields[4] == "0") entry.is_root = false;
        else throw std::runtime_error("policy line " + std::to_string(line_number) + ": invalid is_root");
        entry.reject_reason = std::string(fields[5]);

        if (entry.reject_reason.empty()) {
            if (entry.canonical.empty()) {
                throw std::runtime_error("policy line " + std::to_string(line_number) + ": usable row has empty canonical_go_id");
            }
        } else if (!entry.canonical.empty()) {
            throw std::runtime_error("policy line " + std::to_string(line_number) + ": rejected row has canonical_go_id");
        }

        auto [it, inserted] = policy.emplace(std::string(fields[0]), std::move(entry));
        if (!inserted) {
            throw std::runtime_error("duplicate policy input_go_id at line " + std::to_string(line_number));
        }
    }
    return policy;
}

std::string_view parse_v2_accession(std::string_view header) {
    const std::size_t first = header.find('|');
    const std::size_t second = first == std::string_view::npos ? std::string_view::npos : header.find('|', first + 1);
    if (first == std::string_view::npos || second == std::string_view::npos || second == first + 1) {
        throw std::runtime_error("V2 FASTA header does not contain >sp|PRIMARY|ENTRY or >tr|PRIMARY|ENTRY");
    }
    return header.substr(first + 1, second - first - 1);
}

std::uint64_t index_v2_fasta(
    const fs::path& path,
    ExactStringSet& membership,
    std::uint64_t progress_every
) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open V2 FASTA: " + path.string());
    std::string line;
    std::uint64_t count = 0;
    const auto started = std::chrono::steady_clock::now();
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] != '>') continue;
        const std::string_view accession = parse_v2_accession(line);
        if (!membership.insert(accession)) {
            throw std::runtime_error("duplicate primary accession in V2 FASTA: " + std::string(accession));
        }
        ++count;
        if (progress_every && count % progress_every == 0) {
            const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
            const double rate = elapsed > 0.0 ? static_cast<double>(count) / elapsed : 0.0;
            std::cerr << "[01B1] indexed V2 records=" << count << " rate=" << static_cast<std::uint64_t>(rate)
                      << "/s arena_bytes=" << membership.arena_bytes() << " hash_capacity=" << membership.capacity() << '\n';
        }
    }
    return count;
}

bool has_explicit_not(std::string_view qualifier) {
    std::size_t start = 0;
    while (start <= qualifier.size()) {
        const std::size_t pos = qualifier.find('|', start);
        const std::string_view token = pos == std::string_view::npos
            ? qualifier.substr(start)
            : qualifier.substr(start, pos - start);
        if (!token.empty() && token == "NOT") return true;
        if (pos == std::string_view::npos) break;
        start = pos + 1;
    }
    return false;
}

std::optional<std::string_view> expected_namespace(std::string_view aspect) {
    if (aspect == "F") return "molecular_function";
    if (aspect == "P") return "biological_process";
    if (aspect == "C") return "cellular_component";
    return std::nullopt;
}

struct GoaState {
    std::uint64_t offset = 0;
    std::uint32_t length = 0;
    std::uint8_t bits = 0;
};

constexpr std::uint8_t BIT_MISSING  = 1u << 0;
constexpr std::uint8_t BIT_RETAINED = 1u << 1;
constexpr std::uint8_t BIT_POSITIVE = 1u << 2;
constexpr std::uint8_t BIT_NEGATIVE = 1u << 3;
constexpr std::uint8_t BIT_ROOT     = 1u << 4;

class GoaStates {
public:
    void add_group(std::string_view accession, std::uint8_t bits) {
        if (accession.size() > std::numeric_limits<std::uint32_t>::max()) {
            throw std::overflow_error("GOA accession exceeds uint32 length capacity");
        }
        if (!records_.empty()) {
            const std::string_view prev = view(records_.back());
            if (prev > accession) sorted_ = false;
        }
        const std::uint64_t offset = arena_.size();
        arena_.insert(arena_.end(), accession.begin(), accession.end());
        records_.push_back(GoaState{offset, static_cast<std::uint32_t>(accession.size()), bits});
    }

    std::string_view view(const GoaState& state) const noexcept {
        return std::string_view(arena_.data() + state.offset, state.length);
    }

    void sort_if_needed() {
        if (sorted_) return;
        std::sort(records_.begin(), records_.end(), [this](const GoaState& a, const GoaState& b) {
            return view(a) < view(b);
        });
        sorted_ = true;
    }

    const std::vector<GoaState>& records() const noexcept { return records_; }
    bool was_sorted() const noexcept { return sorted_; }
    std::uint64_t arena_bytes() const noexcept { return arena_.size(); }

private:
    std::vector<char> arena_;
    std::vector<GoaState> records_;
    bool sorted_ = true;
};

struct RowStats {
    std::uint64_t input_data_row_count = 0;
    std::uint64_t retained_annotation_count = 0;
    std::uint64_t positive_annotation_count = 0;
    std::uint64_t not_annotation_count = 0;
    std::uint64_t remapped_annotation_count = 0;
    std::map<std::string, std::uint64_t> discard_reason_counts;
};

struct AccessionStats {
    std::uint64_t goa_accession_count = 0;
    std::uint64_t missing_accession_count = 0;
    std::uint64_t retained_accession_count = 0;
    std::uint64_t positive_accession_count = 0;
    std::uint64_t negative_accession_count = 0;
    std::uint64_t only_not_accession_count = 0;
    std::uint64_t root_only_accession_count = 0;
};

void write_normalized_row(
    std::ostream& out,
    const std::array<std::string_view, 17>& cols,
    std::string_view canonical_go
) {
    for (std::size_t i = 0; i < cols.size(); ++i) {
        if (i) out.put('\t');
        if (i == 4) out.write(canonical_go.data(), static_cast<std::streamsize>(canonical_go.size()));
        else out.write(cols[i].data(), static_cast<std::streamsize>(cols[i].size()));
    }
    out.put('\n');
}

std::array<std::string_view, 17> split_gaf17(std::string_view line, std::uint64_t line_number) {
    std::array<std::string_view, 17> out{};
    std::size_t field = 0;
    std::size_t start = 0;
    for (;;) {
        const std::size_t pos = line.find('\t', start);
        if (field >= out.size()) {
            throw std::runtime_error("GAF line " + std::to_string(line_number) + ": expected 17 columns, found more than 17");
        }
        if (pos == std::string_view::npos) {
            out[field++] = line.substr(start);
            break;
        }
        out[field++] = line.substr(start, pos - start);
        start = pos + 1;
    }
    if (field != out.size()) {
        throw std::runtime_error("GAF line " + std::to_string(line_number) + ": expected 17 columns, found " + std::to_string(field));
    }
    return out;
}

RowStats normalize_goa(
    const fs::path& goa_path,
    const PolicyMap& policy,
    const ExactStringSet& membership,
    const fs::path& normalized_tmp,
    GoaStates& states,
    std::uint64_t progress_every
) {
    std::ifstream in(goa_path);
    if (!in) throw std::runtime_error("cannot open GOA GAF: " + goa_path.string());
    std::ofstream out(normalized_tmp);
    if (!out) throw std::runtime_error("cannot create normalized GAF temp: " + normalized_tmp.string());

    RowStats stats;
    bool saw_gaf_version = false;
    std::string line;
    std::uint64_t line_number = 0;

    std::string current_accession;
    bool have_current = false;
    bool current_in_fasta = false;
    std::uint8_t current_bits = 0;

    auto finalize_current = [&]() {
        if (!have_current) return;
        states.add_group(current_accession, current_bits);
        have_current = false;
        current_accession.clear();
        current_bits = 0;
    };

    const auto started = std::chrono::steady_clock::now();

    while (std::getline(in, line)) {
        ++line_number;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        if (line[0] == '!') {
            if (ascii_ci_starts_with(line, "!gaf-version: 2")) saw_gaf_version = true;
            out << line << '\n';
            continue;
        }

        ++stats.input_data_row_count;
        const auto cols = split_gaf17(line, line_number);

        if (cols[0] == "ComplexPortal" || cols[0] == "RNAcentral") {
            ++stats.discard_reason_counts["IGNORED_DB"];
            continue;
        }

        const std::string_view accession = cols[1];
        if (!have_current || accession != current_accession) {
            finalize_current();
            current_accession.assign(accession.data(), accession.size());
            have_current = true;
            current_in_fasta = membership.contains(accession);
            current_bits = current_in_fasta ? 0 : BIT_MISSING;
        }

        if (!current_in_fasta) {
            ++stats.discard_reason_counts["GOA_ONLY"];
            continue;
        }

        auto it = policy.find(cols[4]);
        if (it == policy.end()) {
            ++stats.discard_reason_counts["UNKNOWN_GO"];
            continue;
        }
        const PolicyEntry& entry = it->second;
        if (!entry.reject_reason.empty()) {
            ++stats.discard_reason_counts[entry.reject_reason];
            continue;
        }

        const auto expected = expected_namespace(cols[8]);
        if (!expected || entry.name_space != *expected) {
            ++stats.discard_reason_counts["ASPECT_MISMATCH"];
            continue;
        }

        if (entry.is_root) {
            current_bits |= BIT_ROOT;
            ++stats.discard_reason_counts["ROOT_GO"];
            continue;
        }

        current_bits |= BIT_RETAINED;
        const bool is_not = has_explicit_not(cols[3]);
        if (is_not) {
            ++stats.not_annotation_count;
            current_bits |= BIT_NEGATIVE;
        } else {
            ++stats.positive_annotation_count;
            current_bits |= BIT_POSITIVE;
        }

        if (cols[4] != entry.canonical) ++stats.remapped_annotation_count;
        write_normalized_row(out, cols, entry.canonical);
        ++stats.retained_annotation_count;

        if (progress_every && stats.input_data_row_count % progress_every == 0) {
            const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
            const double rate = elapsed > 0.0 ? static_cast<double>(stats.input_data_row_count) / elapsed : 0.0;
            std::cerr << "[01B1] GOA data rows=" << stats.input_data_row_count
                      << " retained=" << stats.retained_annotation_count
                      << " positive=" << stats.positive_annotation_count
                      << " NOT=" << stats.not_annotation_count
                      << " rate=" << static_cast<std::uint64_t>(rate) << "/s\n";
        }
    }

    finalize_current();
    out.close();
    if (!out) throw std::runtime_error("write failure for normalized GAF temp");
    if (!saw_gaf_version) throw std::runtime_error("GOA input must declare a GAF 2.x !gaf-version header");
    return stats;
}

AccessionStats materialize_accession_lists(
    GoaStates& states,
    const fs::path& positive_tmp,
    const fs::path& only_not_tmp,
    const fs::path& missing_tmp
) {
    states.sort_if_needed();
    std::ofstream positive(positive_tmp);
    std::ofstream only_not(only_not_tmp);
    std::ofstream missing(missing_tmp);
    if (!positive || !only_not || !missing) throw std::runtime_error("cannot create accession-list temp output");

    AccessionStats stats;
    const auto& records = states.records();
    std::size_t i = 0;
    while (i < records.size()) {
        const std::string_view accession = states.view(records[i]);
        std::uint8_t bits = 0;
        std::size_t j = i;
        while (j < records.size() && states.view(records[j]) == accession) {
            bits |= records[j].bits;
            ++j;
        }

        ++stats.goa_accession_count;
        if (bits & BIT_MISSING) {
            ++stats.missing_accession_count;
            missing.write(accession.data(), static_cast<std::streamsize>(accession.size()));
            missing.put('\n');
        }
        if (bits & BIT_RETAINED) ++stats.retained_accession_count;
        if (bits & BIT_POSITIVE) {
            ++stats.positive_accession_count;
            positive.write(accession.data(), static_cast<std::streamsize>(accession.size()));
            positive.put('\n');
        }
        if (bits & BIT_NEGATIVE) ++stats.negative_accession_count;
        if ((bits & BIT_NEGATIVE) && !(bits & BIT_POSITIVE)) {
            ++stats.only_not_accession_count;
            only_not.write(accession.data(), static_cast<std::streamsize>(accession.size()));
            only_not.put('\n');
        }
        if ((bits & BIT_ROOT) && !(bits & BIT_RETAINED)) ++stats.root_only_accession_count;
        i = j;
    }

    positive.close(); only_not.close(); missing.close();
    if (!positive || !only_not || !missing) throw std::runtime_error("write failure for accession-list temp output");
    return stats;
}

void write_discard_counts(const fs::path& path, const RowStats& stats) {
    std::ofstream out(path);
    if (!out) throw std::runtime_error("cannot create discard counts temp: " + path.string());
    out << "reason\trow_count\n";
    for (const auto& [reason, count] : stats.discard_reason_counts) {
        out << reason << '\t' << count << '\n';
    }
    out.close();
    if (!out) throw std::runtime_error("write failure for discard counts temp");
}

void write_stats_json(
    const fs::path& path,
    const fs::path& policy_path,
    const fs::path& goa_path,
    const fs::path& v2_path,
    std::uint64_t fasta_count,
    const AccessionStats& a,
    const RowStats& r,
    double elapsed_seconds,
    std::uint64_t membership_arena_bytes,
    std::uint64_t membership_hash_capacity,
    std::uint64_t goa_state_arena_bytes
) {
    const std::uint64_t shared_accessions = a.goa_accession_count - a.missing_accession_count;
    if (shared_accessions > fasta_count) throw std::logic_error("shared accession count exceeds FASTA accession count");
    const std::uint64_t uniprot_only_accessions = fasta_count - shared_accessions;

    std::ofstream out(path);
    if (!out) throw std::runtime_error("cannot create stats temp: " + path.string());
    out << "{\n";
    out << "  \"stage\": \"01B\",\n";
    out << "  \"implementation_stage\": \"01B1\",\n";
    out << "  \"policy_path\": \"" << json_escape(policy_path.string()) << "\",\n";
    out << "  \"goa_path\": \"" << json_escape(goa_path.string()) << "\",\n";
    out << "  \"uniprot_v2_path\": \"" << json_escape(v2_path.string()) << "\",\n";
    out << "  \"fasta_accession_count\": " << fasta_count << ",\n";
    out << "  \"goa_accession_count\": " << a.goa_accession_count << ",\n";
    out << "  \"shared_accession_count\": " << shared_accessions << ",\n";
    out << "  \"goa_only_accession_count\": " << a.missing_accession_count << ",\n";
    out << "  \"uniprot_only_accession_count\": " << uniprot_only_accessions << ",\n";
    out << "  \"retained_accession_count\": " << a.retained_accession_count << ",\n";
    out << "  \"positive_accession_count\": " << a.positive_accession_count << ",\n";
    out << "  \"negative_accession_count\": " << a.negative_accession_count << ",\n";
    out << "  \"only_not_accession_count\": " << a.only_not_accession_count << ",\n";
    out << "  \"root_only_accession_count\": " << a.root_only_accession_count << ",\n";
    out << "  \"input_data_row_count\": " << r.input_data_row_count << ",\n";
    out << "  \"retained_annotation_count\": " << r.retained_annotation_count << ",\n";
    out << "  \"positive_annotation_count\": " << r.positive_annotation_count << ",\n";
    out << "  \"not_annotation_count\": " << r.not_annotation_count << ",\n";
    out << "  \"remapped_annotation_count\": " << r.remapped_annotation_count << ",\n";
    out << "  \"discard_reason_counts\": {";
    bool first = true;
    for (const auto& [reason, count] : r.discard_reason_counts) {
        out << (first ? "\n" : ",\n")
            << "    \"" << json_escape(reason) << "\": " << count;
        first = false;
    }
    if (!first) out << '\n';
    out << "  },\n";
    out << std::fixed << std::setprecision(6);
    out << "  \"elapsed_seconds\": " << elapsed_seconds << ",\n";
    out << "  \"not_semantics\": {\n";
    out << "    \"normalized_gaf_preserves_explicit_NOT\": true,\n";
    out << "    \"positive_search_whitelist_requires_non_NOT_row\": true,\n";
    out << "    \"only_not_accessions_are_excluded_from_search_whitelist\": true,\n";
    out << "    \"truth_NOT_descendant_veto_is_not_applied_in_stage_01B\": true\n";
    out << "  },\n";
    out << "  \"implementation\": {\n";
    out << "    \"membership\": \"exact_open_addressing_hash_with_byte_comparison\",\n";
    out << "    \"membership_arena_bytes\": " << membership_arena_bytes << ",\n";
    out << "    \"membership_hash_capacity\": " << membership_hash_capacity << ",\n";
    out << "    \"goa_state_arena_bytes\": " << goa_state_arena_bytes << "\n";
    out << "  }\n";
    out << "}\n";
    out.close();
    if (!out) throw std::runtime_error("write failure for stats temp");
}

struct Args {
    fs::path policy;
    fs::path goa;
    fs::path uniprot_v2;
    fs::path output_dir;
    std::uint64_t reserve_v2_records = 0;
    std::uint64_t progress_every = 5'000'000;
    bool overwrite = false;
};

void usage(const char* argv0) {
    std::cerr
        << "Usage: " << argv0 << " --policy FILE --goa FILE --uniprot-v2 FILE --output-dir DIR [options]\n"
        << "Options:\n"
        << "  --reserve-v2-records N   Capacity hint only; does not affect semantics\n"
        << "  --progress-every N       Progress interval (default 5000000; 0 disables)\n"
        << "  --overwrite              Replace reviewed existing outputs\n";
}

Args parse_args(int argc, char** argv) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        const std::string_view key = argv[i];
        auto require_value = [&](std::string_view option) -> std::string_view {
            if (i + 1 >= argc) throw std::invalid_argument(std::string(option) + " requires a value");
            return argv[++i];
        };
        if (key == "--policy") args.policy = std::string(require_value(key));
        else if (key == "--goa") args.goa = std::string(require_value(key));
        else if (key == "--uniprot-v2") args.uniprot_v2 = std::string(require_value(key));
        else if (key == "--output-dir") args.output_dir = std::string(require_value(key));
        else if (key == "--reserve-v2-records") args.reserve_v2_records = parse_u64(require_value(key), key);
        else if (key == "--progress-every") args.progress_every = parse_u64(require_value(key), key);
        else if (key == "--overwrite") args.overwrite = true;
        else if (key == "--help" || key == "-h") {
            usage(argv[0]);
            std::exit(0);
        } else {
            throw std::invalid_argument("unknown argument: " + std::string(key));
        }
    }
    if (args.policy.empty() || args.goa.empty() || args.uniprot_v2.empty() || args.output_dir.empty()) {
        throw std::invalid_argument("--policy, --goa, --uniprot-v2, and --output-dir are required");
    }
    return args;
}

void ensure_input_file(const fs::path& path) {
    if (!fs::is_regular_file(path)) throw std::runtime_error("input file not found: " + path.string());
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Args args = parse_args(argc, argv);
        ensure_input_file(args.policy);
        ensure_input_file(args.goa);
        ensure_input_file(args.uniprot_v2);
        fs::create_directories(args.output_dir);

        const fs::path normalized = args.output_dir / "goa.normalized.gaf";
        const fs::path positive = args.output_dir / "goa.positive_accessions.txt";
        const fs::path only_not = args.output_dir / "goa.only_not_accessions.txt";
        const fs::path missing = args.output_dir / "goa.missing_accessions.txt";
        const fs::path discard = args.output_dir / "goa.discard_reason_counts.tsv";
        const fs::path stats = args.output_dir / "goa.stats.json";
        const std::array<fs::path, 6> finals{normalized, positive, only_not, missing, discard, stats};

        if (!args.overwrite) {
            for (const auto& path : finals) {
                if (fs::exists(path)) {
                    throw std::runtime_error("output already exists; use --overwrite only after review: " + path.string());
                }
            }
        }

        ScopedTemps temps;
        const fs::path normalized_tmp = make_temp_path(normalized);
        const fs::path positive_tmp = make_temp_path(positive);
        const fs::path only_not_tmp = make_temp_path(only_not);
        const fs::path missing_tmp = make_temp_path(missing);
        const fs::path discard_tmp = make_temp_path(discard);
        const fs::path stats_tmp = make_temp_path(stats);
        temps.paths = {normalized_tmp, positive_tmp, only_not_tmp, missing_tmp, discard_tmp, stats_tmp};

        const auto started = std::chrono::steady_clock::now();

        std::cerr << "[01B1] loading GO policy: " << args.policy << '\n';
        const PolicyMap policy = load_policy(args.policy);
        std::cerr << "[01B1] policy rows=" << policy.size() << '\n';

        std::cerr << "[01B1] indexing complete V2 FASTA: " << args.uniprot_v2 << '\n';
        ExactStringSet membership(args.reserve_v2_records);
        const std::uint64_t fasta_count = index_v2_fasta(args.uniprot_v2, membership, args.progress_every);
        std::cerr << "[01B1] indexed V2 records=" << fasta_count
                  << " arena_bytes=" << membership.arena_bytes()
                  << " hash_capacity=" << membership.capacity() << '\n';

        std::cerr << "[01B1] normalizing GOA: " << args.goa << '\n';
        GoaStates goa_states;
        const RowStats row_stats = normalize_goa(
            args.goa, policy, membership, normalized_tmp, goa_states, args.progress_every
        );

        std::cerr << "[01B1] materializing sorted accession lists...\n";
        const AccessionStats accession_stats = materialize_accession_lists(
            goa_states, positive_tmp, only_not_tmp, missing_tmp
        );

        write_discard_counts(discard_tmp, row_stats);
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        write_stats_json(
            stats_tmp,
            args.policy,
            args.goa,
            args.uniprot_v2,
            fasta_count,
            accession_stats,
            row_stats,
            elapsed,
            membership.arena_bytes(),
            membership.capacity(),
            goa_states.arena_bytes()
        );

        install_atomic(normalized_tmp, normalized);
        install_atomic(positive_tmp, positive);
        install_atomic(only_not_tmp, only_not);
        install_atomic(missing_tmp, missing);
        install_atomic(discard_tmp, discard);
        install_atomic(stats_tmp, stats);
        temps.keep = true;

        std::cerr << "[01B1] done elapsed_seconds=" << elapsed
                  << " positive_accessions=" << accession_stats.positive_accession_count
                  << " only_NOT_accessions=" << accession_stats.only_not_accession_count
                  << " missing_accessions=" << accession_stats.missing_accession_count << '\n';
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << '\n';
        return 2;
    }
}
