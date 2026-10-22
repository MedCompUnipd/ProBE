// Exact large-pair extension of the frozen canonical V2 DP contract.
//
// The frozen translation unit is intentionally included with only its main
// symbol renamed. This reuses its certified region, predecessor/endpoint tie
// rules, records, and TSV formatter without editing the validated source.
// The rolling recurrence below replaces only trace storage. Each state carries
// statistics of precisely the path selected by the frozen predecessor order.
// Statistics never participate in score comparisons. Free leading overhangs
// start an empty path; every penalized residue is counted. Gap-run counts are
// invariant under reversal, so forward accumulation equals frozen traceback.
//
// Persistent checkpoints include the previous frontier, the processed prefix
// of the current frontier, the endpoint winner and its statistics. A quantum
// can end inside a row. No cell before the saved cursor is reevaluated.
// Checkpoint bytes have an explicit little-endian format and a streaming
// SHA-256 checksum. They bind full input sequences, metadata, certified plan,
// caller identity, and the checkpoint format/scoring version.

#define main probe_bounded_frozen_reference_main
#include "probe_directional_glocal_bounded_dp_v2.cpp"
#undef main

#include <array>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <filesystem>
#include <functional>
#include <iomanip>
#include <memory>
#include <sstream>
#include <unordered_set>

#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

namespace {

constexpr const char* LINEAR_BACKEND = "certified-rolling-stats-v1";
constexpr const char* CHECKPOINT_MAGIC = "probe-affine-rolling-checkpoint-v1";
constexpr std::uint64_t MEMORY_MARGIN = 1024ULL * 1024ULL;
volatile std::sig_atomic_t requested_signal = 0;

void request_checkpoint(int signal_number) {
    requested_signal = signal_number;
}

void checkpoint_if_parent_dies(pid_t expected_parent) {
#ifdef PR_SET_PDEATHSIG
    // subprocess.run cannot clean up after a SIGKILL of its Python parent.
    // Request an ordinary checkpoint signal from the kernel in that case.
    // The handler is installed first; the parent check closes the race where
    // the parent exits immediately before prctl registers the notification.
    if (::prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) {
        throw std::runtime_error("cannot register DP parent-death checkpoint");
    }
    if (expected_parent == 1 || ::getppid() != expected_parent) {
        requested_signal = SIGTERM;
    }
#else
    (void)expected_parent;
#endif
}

struct PathState {
    std::int32_t score = NEG;
    std::int32_t target_start = 0;
    std::int32_t candidate_start = 0;
    std::uint32_t matches = 0;
    std::uint32_t mismatches = 0;
    std::uint32_t gap_in_target = 0;
    std::uint32_t gap_in_candidate = 0;
    std::uint32_t gap_openings = 0;
};

// plan_glocal limits both lengths to INT_MAX/8. Every path count is bounded by
// n+m < 2^29, so uint32 counters preserve every value of the frozen uint64 API.
static_assert(sizeof(PathState) == 32, "planner expects 32-byte DP states");
using FrontierCell = std::array<PathState, 3>;
static_assert(sizeof(FrontierCell) == 96, "planner expects 96-byte DP cells");

std::uint64_t columns(const PathState& state) {
    return static_cast<std::uint64_t>(state.matches) + state.mismatches +
        state.gap_in_target + state.gap_in_candidate;
}

class Sha256 {
public:
    void update(const void* data, std::size_t length) {
        const auto* bytes = static_cast<const std::uint8_t*>(data);
        total_bytes_ += length;
        while (length > 0) {
            const auto take = std::min(length, block_.size() - buffered_);
            std::memcpy(block_.data() + buffered_, bytes, take);
            buffered_ += take;
            bytes += take;
            length -= take;
            if (buffered_ == block_.size()) {
                transform();
                buffered_ = 0;
            }
        }
    }

    std::array<std::uint8_t, 32> finish() {
        const std::uint64_t bit_length = total_bytes_ * 8;
        block_[buffered_++] = 0x80;
        if (buffered_ > 56) {
            std::fill(block_.begin() + buffered_, block_.end(), 0);
            transform();
            buffered_ = 0;
        }
        std::fill(block_.begin() + buffered_, block_.begin() + 56, 0);
        for (int index = 0; index < 8; ++index) {
            block_[63 - index] = static_cast<std::uint8_t>(
                bit_length >> (index * 8)
            );
        }
        transform();
        std::array<std::uint8_t, 32> digest{};
        for (std::size_t index = 0; index < state_.size(); ++index) {
            for (int byte = 0; byte < 4; ++byte) {
                digest[index * 4 + byte] = static_cast<std::uint8_t>(
                    state_[index] >> (24 - byte * 8)
                );
            }
        }
        return digest;
    }

private:
    static std::uint32_t rotate(std::uint32_t value, int bits) {
        return (value >> bits) | (value << (32 - bits));
    }

    void transform() {
        static constexpr std::array<std::uint32_t, 64> constants = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
            0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
            0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
            0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
            0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
            0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
            0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
            0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
        };
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index) {
            const auto offset = index * 4;
            words[index] =
                (static_cast<std::uint32_t>(block_[offset]) << 24) |
                (static_cast<std::uint32_t>(block_[offset + 1]) << 16) |
                (static_cast<std::uint32_t>(block_[offset + 2]) << 8) |
                static_cast<std::uint32_t>(block_[offset + 3]);
        }
        for (std::size_t index = 16; index < 64; ++index) {
            const auto a = words[index - 15];
            const auto b = words[index - 2];
            const auto small0 = rotate(a, 7) ^ rotate(a, 18) ^ (a >> 3);
            const auto small1 = rotate(b, 17) ^ rotate(b, 19) ^ (b >> 10);
            words[index] = words[index - 16] + small0 +
                words[index - 7] + small1;
        }
        auto a = state_[0];
        auto b = state_[1];
        auto c = state_[2];
        auto d = state_[3];
        auto e = state_[4];
        auto f = state_[5];
        auto g = state_[6];
        auto h = state_[7];
        for (std::size_t index = 0; index < 64; ++index) {
            const auto big1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
            const auto choose = (e & f) ^ (~e & g);
            const auto temp1 = h + big1 + choose + constants[index] +
                words[index];
            const auto big0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto temp2 = big0 + majority;
            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }
        state_[0] += a;
        state_[1] += b;
        state_[2] += c;
        state_[3] += d;
        state_[4] += e;
        state_[5] += f;
        state_[6] += g;
        state_[7] += h;
    }

    std::array<std::uint32_t, 8> state_ = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };
    std::array<std::uint8_t, 64> block_{};
    std::size_t buffered_ = 0;
    std::uint64_t total_bytes_ = 0;
};

void sync_file(const std::filesystem::path& path, bool directory = false) {
    const int descriptor = ::open(
        path.c_str(), O_RDONLY | (directory ? O_DIRECTORY : 0)
    );
    if (descriptor < 0) {
        throw std::runtime_error("cannot open for fsync: " + path.string());
    }
    const int result = ::fsync(descriptor);
    const int close_result = ::close(descriptor);
    if (result != 0 || close_result != 0) {
        throw std::runtime_error("fsync failed: " + path.string());
    }
}

void install_atomic(
    const std::filesystem::path& temporary,
    const std::filesystem::path& destination
) {
    sync_file(temporary);
    std::filesystem::rename(temporary, destination);
    const auto parent = destination.has_parent_path()
        ? destination.parent_path() : std::filesystem::path(".");
    sync_file(parent, true);
}

class CheckedWriter {
public:
    explicit CheckedWriter(const std::filesystem::path& path)
        : stream_(path, std::ios::binary | std::ios::trunc) {
        if (!stream_) throw std::runtime_error("cannot write checkpoint");
    }

    void bytes(const void* data, std::size_t length) {
        stream_.write(static_cast<const char*>(data), length);
        if (!stream_) throw std::runtime_error("checkpoint write failed");
        hash_.update(data, length);
    }

    void u32(std::uint32_t value) {
        std::array<std::uint8_t, 4> buffer{};
        for (int index = 0; index < 4; ++index) {
            buffer[index] = static_cast<std::uint8_t>(value >> (index * 8));
        }
        bytes(buffer.data(), buffer.size());
    }

    void i32(std::int32_t value) { u32(static_cast<std::uint32_t>(value)); }

    void u64(std::uint64_t value) {
        u32(static_cast<std::uint32_t>(value));
        u32(static_cast<std::uint32_t>(value >> 32));
    }

    void string(const std::string& value) {
        u64(value.size());
        bytes(value.data(), value.size());
    }

    void finish() {
        const auto digest = hash_.finish();
        stream_.write(
            reinterpret_cast<const char*>(digest.data()), digest.size()
        );
        stream_.flush();
        if (!stream_) throw std::runtime_error("checkpoint flush failed");
        stream_.close();
        if (!stream_) throw std::runtime_error("checkpoint close failed");
    }

private:
    std::ofstream stream_;
    Sha256 hash_;
};

class CheckedReader {
public:
    explicit CheckedReader(const std::filesystem::path& path)
        : stream_(path, std::ios::binary) {
        if (!stream_) throw std::runtime_error("cannot read checkpoint");
    }

    void bytes(void* data, std::size_t length) {
        stream_.read(static_cast<char*>(data), length);
        if (!stream_) throw std::runtime_error("truncated checkpoint");
        hash_.update(data, length);
    }

    std::uint32_t u32() {
        std::array<std::uint8_t, 4> buffer{};
        bytes(buffer.data(), buffer.size());
        std::uint32_t value = 0;
        for (int index = 0; index < 4; ++index) {
            value |= static_cast<std::uint32_t>(buffer[index]) << (index * 8);
        }
        return value;
    }

    std::int32_t i32() {
        const auto value = u32();
        const auto signed_value = value <= 0x7fffffffU
            ? static_cast<std::int64_t>(value)
            : static_cast<std::int64_t>(value) - 0x100000000LL;
        return static_cast<std::int32_t>(signed_value);
    }

    std::uint64_t u64() {
        const auto low = u32();
        const auto high = u32();
        return static_cast<std::uint64_t>(low) |
            (static_cast<std::uint64_t>(high) << 32);
    }

    void expect_string(const std::string& expected) {
        if (u64() != expected.size()) {
            throw std::runtime_error("checkpoint identity mismatch");
        }
        std::array<char, 4096> buffer{};
        for (std::size_t offset = 0; offset < expected.size();) {
            const auto length = std::min(buffer.size(), expected.size() - offset);
            bytes(buffer.data(), length);
            if (std::memcmp(buffer.data(), expected.data() + offset, length) != 0) {
                throw std::runtime_error("checkpoint identity mismatch");
            }
            offset += length;
        }
    }

    void expect_u64(std::uint64_t expected) {
        if (u64() != expected) {
            throw std::runtime_error("checkpoint plan mismatch");
        }
    }

    void expect_i32(std::int32_t expected) {
        if (i32() != expected) {
            throw std::runtime_error("checkpoint plan mismatch");
        }
    }

    void finish() {
        const auto actual = hash_.finish();
        std::array<std::uint8_t, 32> expected{};
        stream_.read(reinterpret_cast<char*>(expected.data()), expected.size());
        if (!stream_ || actual != expected || stream_.peek() != EOF) {
            throw std::runtime_error("checkpoint SHA256 checksum mismatch");
        }
    }

private:
    std::ifstream stream_;
    Sha256 hash_;
};

void write_state(CheckedWriter& out, const PathState& state) {
    out.i32(state.score);
    out.i32(state.target_start);
    out.i32(state.candidate_start);
    out.u32(state.matches);
    out.u32(state.mismatches);
    out.u32(state.gap_in_target);
    out.u32(state.gap_in_candidate);
    out.u32(state.gap_openings);
}

PathState read_state(CheckedReader& in, int n, int m) {
    PathState state;
    state.score = in.i32();
    state.target_start = in.i32();
    state.candidate_start = in.i32();
    state.matches = in.u32();
    state.mismatches = in.u32();
    state.gap_in_target = in.u32();
    state.gap_in_candidate = in.u32();
    state.gap_openings = in.u32();
    if (state.target_start < 0 || state.target_start > n ||
        state.candidate_start < 0 || state.candidate_start > m ||
        columns(state) > static_cast<std::uint64_t>(n) + m ||
        state.gap_openings > state.gap_in_target + state.gap_in_candidate ||
        state.score < NEG || state.score > MATCH_SCORE * std::max(n, m)) {
        throw std::runtime_error("invalid checkpoint path state");
    }
    return state;
}

struct RollingCursor {
    int row = 0;
    int next_column = 0;
    int previous_low = 0;
    int previous_high = -1;
    int current_low = 0;
    int current_high = -1;
    std::uint64_t processed_cells = 0;
    EndPoint endpoint;
    PathState endpoint_path;
    std::vector<FrontierCell> previous;
    std::vector<FrontierCell> current;
};

int row_low(const BandPlan& plan, int i) {
    return std::max(0, i + plan.diagonal_min);
}

int row_high(const BandPlan& plan, int i, int m) {
    return std::min(m, i + plan.diagonal_max);
}

int maximum_width(const BandPlan& plan, int m) {
    return std::min(m + 1, plan.diagonal_max - plan.diagonal_min + 1);
}

void prepare_current_row(RollingCursor& cursor, const BandPlan& plan, int m) {
    cursor.current_low = row_low(plan, cursor.row);
    cursor.current_high = row_high(plan, cursor.row, m);
    if (cursor.current_low > cursor.current_high) {
        throw std::runtime_error("empty row in certified rolling region");
    }
    cursor.next_column = cursor.current_low;
    std::fill(
        cursor.current.begin(),
        cursor.current.begin() + cursor.current_high - cursor.current_low + 1,
        FrontierCell{}
    );
}

void checkpoint_identity(
    CheckedWriter& out,
    const std::string& identity,
    std::size_t job_index,
    const Job& job,
    const TargetRec& target,
    const CandidateRec& candidate,
    Mode mode,
    const BandPlan& plan
) {
    out.string(CHECKPOINT_MAGIC);
    out.string(LINEAR_BACKEND);
    out.string(identity);
    out.u64(job_index);
    out.string(job.release);
    out.string(job.target_id);
    out.u64(job.rid);
    out.string(job.job_scope);
    out.string(job.discovery_channel);
    out.string(target.accession);
    out.string(candidate.accession);
    out.string(target.seq);
    out.string(candidate.seq);
    out.i32(mode == Mode::TARGET_GLOBAL ? 0 : 1);
    out.i32(MATCH_SCORE);
    out.i32(MISMATCH_SCORE);
    out.i32(GAP_OPEN);
    out.i32(GAP_EXTEND);
    out.i32(plan.lower_bound);
    out.i32(plan.diagonal_min);
    out.i32(plan.diagonal_max);
    out.u64(plan.cells);
    out.u64(plan.full_cells);
}

void verify_checkpoint_identity(
    CheckedReader& in,
    const std::string& identity,
    std::size_t job_index,
    const Job& job,
    const TargetRec& target,
    const CandidateRec& candidate,
    Mode mode,
    const BandPlan& plan
) {
    in.expect_string(CHECKPOINT_MAGIC);
    in.expect_string(LINEAR_BACKEND);
    in.expect_string(identity);
    in.expect_u64(job_index);
    in.expect_string(job.release);
    in.expect_string(job.target_id);
    in.expect_u64(job.rid);
    in.expect_string(job.job_scope);
    in.expect_string(job.discovery_channel);
    in.expect_string(target.accession);
    in.expect_string(candidate.accession);
    in.expect_string(target.seq);
    in.expect_string(candidate.seq);
    in.expect_i32(mode == Mode::TARGET_GLOBAL ? 0 : 1);
    in.expect_i32(MATCH_SCORE);
    in.expect_i32(MISMATCH_SCORE);
    in.expect_i32(GAP_OPEN);
    in.expect_i32(GAP_EXTEND);
    in.expect_i32(plan.lower_bound);
    in.expect_i32(plan.diagonal_min);
    in.expect_i32(plan.diagonal_max);
    in.expect_u64(plan.cells);
    in.expect_u64(plan.full_cells);
}

void write_cursor(CheckedWriter& out, const RollingCursor& cursor, int n) {
    out.i32(cursor.row);
    out.i32(cursor.next_column);
    out.u64(cursor.processed_cells);
    out.i32(cursor.endpoint.score);
    out.i32(cursor.endpoint.i);
    out.i32(cursor.endpoint.j);
    out.i32(cursor.endpoint.state);
    write_state(out, cursor.endpoint_path);
    if (cursor.row > n) return;
    for (int j = cursor.previous_low; j <= cursor.previous_high; ++j) {
        for (const auto& state : cursor.previous[j - cursor.previous_low]) {
            write_state(out, state);
        }
    }
    for (int j = cursor.current_low; j < cursor.next_column; ++j) {
        for (const auto& state : cursor.current[j - cursor.current_low]) {
            write_state(out, state);
        }
    }
}

void read_cursor(
    CheckedReader& in,
    RollingCursor& cursor,
    const BandPlan& plan,
    Mode mode,
    int n,
    int m
) {
    cursor.row = in.i32();
    cursor.next_column = in.i32();
    cursor.processed_cells = in.u64();
    cursor.endpoint.score = in.i32();
    cursor.endpoint.i = in.i32();
    cursor.endpoint.j = in.i32();
    cursor.endpoint.state = in.i32();
    cursor.endpoint_path = read_state(in, n, m);
    if (cursor.row < 0 || cursor.row > n + 1) {
        throw std::runtime_error("invalid checkpoint row");
    }
    std::uint64_t expected_cells = 0;
    for (int i = 0; i < cursor.row; ++i) {
        expected_cells += row_high(plan, i, m) - row_low(plan, i) + 1;
    }
    if (cursor.row <= n) {
        cursor.current_low = row_low(plan, cursor.row);
        cursor.current_high = row_high(plan, cursor.row, m);
        if (cursor.next_column < cursor.current_low ||
            cursor.next_column > cursor.current_high) {
            throw std::runtime_error("invalid checkpoint column cursor");
        }
        expected_cells += cursor.next_column - cursor.current_low;
    } else if (cursor.next_column != 0) {
        throw std::runtime_error("invalid completed checkpoint cursor");
    }
    if (expected_cells != cursor.processed_cells || expected_cells > plan.cells) {
        throw std::runtime_error("checkpoint progress/cell count mismatch");
    }
    const auto& endpoint = cursor.endpoint;
    if (endpoint.state == -1) {
        if (endpoint.score != NEG || endpoint.i != -1 || endpoint.j != -1) {
            throw std::runtime_error("invalid empty checkpoint endpoint");
        }
    } else if (endpoint.state < 0 || endpoint.state > 2 ||
        endpoint.i < 1 || endpoint.i > n || endpoint.j < 0 || endpoint.j > m ||
        endpoint.score != cursor.endpoint_path.score ||
        (mode == Mode::TARGET_GLOBAL && endpoint.i != n) ||
        (mode == Mode::CANDIDATE_GLOBAL && endpoint.j != m) ||
        endpoint.i > cursor.row ||
        (endpoint.i == cursor.row && endpoint.j >= cursor.next_column)) {
        throw std::runtime_error("invalid checkpoint endpoint");
    }
    if (cursor.row > n) return;
    if (cursor.row > 0) {
        cursor.previous_low = row_low(plan, cursor.row - 1);
        cursor.previous_high = row_high(plan, cursor.row - 1, m);
        for (int j = cursor.previous_low; j <= cursor.previous_high; ++j) {
            for (auto& state : cursor.previous[j - cursor.previous_low]) {
                state = read_state(in, n, m);
            }
        }
    }
    for (int j = cursor.current_low; j < cursor.next_column; ++j) {
        for (auto& state : cursor.current[j - cursor.current_low]) {
            state = read_state(in, n, m);
        }
    }
}

struct LinearOptions {
    std::string targets;
    std::string jobs;
    std::string start_store;
    std::string end_store;
    std::string output;
    std::string summary;
    std::string start_release;
    std::string end_release;
    std::string checkpoint_directory;
    std::string checkpoint_identity;
    std::string progress_json;
    int threads = 1;
    std::uint64_t checkpoint_rows = 256;
    std::uint64_t memory_limit = 1024ULL * 1024ULL * 1024ULL;
    std::uint64_t quantum = 0;
    std::uint64_t stop_after_rows = 0;
};

struct InvocationProgress {
    std::uint64_t computed_cells = 0;
    std::uint64_t resumed_cells = 0;
    std::uint64_t completed_rows = 0;
    std::uint64_t required_memory = 0;
    std::uint64_t frontier_memory = 0;
    std::size_t job_index = 0;
    Mode mode = Mode::TARGET_GLOBAL;
    int row = 0;
    int next_column = 0;
};

std::string direction_name(Mode mode) {
    return mode == Mode::TARGET_GLOBAL ? "TARGET_GLOBAL" : "CANDIDATE_GLOBAL";
}

std::filesystem::path checkpoint_path(
    const LinearOptions& options, std::size_t job_index, Mode mode
) {
    std::ostringstream filename;
    filename << "job-" << std::setfill('0') << std::setw(8) << job_index
        << '-' << direction_name(mode) << ".bin";
    return std::filesystem::path(options.checkpoint_directory) / filename.str();
}

void write_progress(
    const LinearOptions& options,
    const InvocationProgress& progress,
    const std::string& status
) {
    if (options.progress_json.empty()) return;
    const std::filesystem::path destination(options.progress_json);
    const std::filesystem::path temporary(options.progress_json + ".tmp");
    std::ofstream out(temporary, std::ios::trunc);
    if (!out) throw std::runtime_error("cannot write rolling progress");
    out << "{\n"
        << "  \"schema\": \"probe-rolling-progress-v1\",\n"
        << "  \"backend\": \"" << LINEAR_BACKEND << "\",\n"
        << "  \"identity\": \"" << options.checkpoint_identity << "\",\n"
        << "  \"status\": \"" << status << "\",\n"
        << "  \"job_index\": " << progress.job_index << ",\n"
        << "  \"direction\": \"" << direction_name(progress.mode) << "\",\n"
        << "  \"row\": " << progress.row << ",\n"
        << "  \"next_column\": " << progress.next_column << ",\n"
        << "  \"invocation_computed_cells\": " << progress.computed_cells << ",\n"
        << "  \"resumed_cells\": " << progress.resumed_cells << ",\n"
        << "  \"completed_cells\": "
        << progress.resumed_cells + progress.computed_cells << ",\n"
        << "  \"required_memory_bytes\": " << progress.required_memory << ",\n"
        << "  \"frontier_memory_bytes\": " << progress.frontier_memory << ",\n"
        << "  \"execution_threads\": 1,\n"
        << "  \"reserved_threads\": " << options.threads << ",\n"
        << "  \"quantum_cells\": " << options.quantum << ",\n"
        << "  \"checkpoint_rows\": " << options.checkpoint_rows << ",\n"
        << "  \"signal\": " << requested_signal << "\n"
        << "}\n";
    out.flush();
    if (!out) throw std::runtime_error("rolling progress flush failed");
    out.close();
    if (!out) throw std::runtime_error("rolling progress close failed");
    install_atomic(temporary, destination);
}

class CheckpointLock {
public:
    explicit CheckpointLock(const std::string& directory) {
        if (directory.empty()) return;
        std::filesystem::create_directories(directory);
        const auto path = std::filesystem::path(directory) / "execution.lock";
        descriptor_ = ::open(path.c_str(), O_RDWR | O_CREAT, 0600);
        if (descriptor_ < 0) {
            throw std::runtime_error("cannot open rolling checkpoint lock");
        }
        // The outer stage lock serializes callers. After a parent crash, its
        // native child may still be finishing a durable checkpoint: wait for
        // that writer instead of rejecting a valid immediate resume. A signal
        // only sets requested_signal; acquire the lock before reading/writing
        // any checkpoint, then exit through the normal checkpoint path.
        while (::flock(descriptor_, LOCK_EX) != 0) {
            if (errno == EINTR) continue;
            ::close(descriptor_);
            descriptor_ = -1;
            throw std::runtime_error("cannot acquire rolling checkpoint lock");
        }
    }

    ~CheckpointLock() {
        if (descriptor_ >= 0) ::close(descriptor_);
    }

    CheckpointLock(const CheckpointLock&) = delete;
    CheckpointLock& operator=(const CheckpointLock&) = delete;

private:
    int descriptor_ = -1;
};

struct QuantumCheckpoint : public std::exception {};

void save_checkpoint(
    const LinearOptions& options,
    std::size_t job_index,
    const Job& job,
    const TargetRec& target,
    const CandidateRec& candidate,
    Mode mode,
    const BandPlan& plan,
    const RollingCursor& cursor
) {
    if (options.checkpoint_directory.empty()) return;
    const auto destination = checkpoint_path(options, job_index, mode);
    auto temporary = destination;
    temporary += ".tmp";
    CheckedWriter writer(temporary);
    checkpoint_identity(
        writer, options.checkpoint_identity, job_index, job,
        target, candidate, mode, plan
    );
    write_cursor(writer, cursor, static_cast<int>(target.seq.size()));
    writer.finish();
    install_atomic(temporary, destination);
}

void evaluate_cell(
    RollingCursor& cursor,
    const std::string& target,
    const std::string& candidate,
    Mode mode
) {
    const int i = cursor.row;
    const int j = cursor.next_column;
    auto& cell = cursor.current[j - cursor.current_low];
    if (i == 0) {
        if (j == 0) {
            cell[0].score = 0;
        } else if (mode == Mode::TARGET_GLOBAL) {
            cell[2].score = 0;
            cell[2].candidate_start = j;
        } else {
            cell[2].score = gap_score(j);
            cell[2].gap_in_target = j;
            cell[2].gap_openings = 1;
        }
        return;
    }
    if (j == 0) {
        if (mode == Mode::CANDIDATE_GLOBAL) {
            cell[1].score = 0;
            cell[1].target_start = i;
        } else {
            cell[1].score = gap_score(i);
            cell[1].gap_in_candidate = i;
            cell[1].gap_openings = 1;
        }
        return;
    }
    static const FrontierCell excluded{};
    const auto& above = j >= cursor.previous_low && j <= cursor.previous_high
        ? cursor.previous[j - cursor.previous_low] : excluded;
    const auto& diagonal = j - 1 >= cursor.previous_low &&
        j - 1 <= cursor.previous_high
        ? cursor.previous[j - 1 - cursor.previous_low] : excluded;
    const auto& left = j > cursor.current_low
        ? cursor.current[j - 1 - cursor.current_low] : excluded;

    const auto mc = best3(
        diagonal[0].score, 0, diagonal[1].score, 1, diagonal[2].score, 2
    );
    if (mc.score > NEG / 2) {
        cell[0] = diagonal[mc.pred];
        const bool literal_match = target[i - 1] == candidate[j - 1];
        cell[0].score = mc.score + (literal_match ? MATCH_SCORE : MISMATCH_SCORE);
        if (literal_match) ++cell[0].matches;
        else ++cell[0].mismatches;
    }
    const auto add_gap_score = [](const PathState& state, int cost) {
        return state.score > NEG / 2 ? state.score + cost : NEG;
    };
    const auto xc = best3(
        add_gap_score(above[1], GAP_EXTEND), 1,
        add_gap_score(above[0], GAP_OPEN), 0,
        add_gap_score(above[2], GAP_OPEN), 2
    );
    if (xc.score > NEG / 2) {
        cell[1] = above[xc.pred];
        if (xc.pred != 1 || columns(cell[1]) == 0) ++cell[1].gap_openings;
        ++cell[1].gap_in_candidate;
        cell[1].score = xc.score;
    }
    const auto yc = best3(
        add_gap_score(left[2], GAP_EXTEND), 2,
        add_gap_score(left[0], GAP_OPEN), 0,
        add_gap_score(left[1], GAP_OPEN), 1
    );
    if (yc.score > NEG / 2) {
        cell[2] = left[yc.pred];
        if (yc.pred != 2 || columns(cell[2]) == 0) ++cell[2].gap_openings;
        ++cell[2].gap_in_target;
        cell[2].score = yc.score;
    }
}

AlignResult align_glocal_rolling(
    const LinearOptions& options,
    std::size_t job_index,
    const Job& job,
    const TargetRec& target,
    const CandidateRec& candidate,
    Mode mode,
    const BandPlan& plan,
    InvocationProgress& progress
) {
    const int n = static_cast<int>(target.seq.size());
    const int m = static_cast<int>(candidate.seq.size());
    const int width = maximum_width(plan, m);
    const auto frontier_bytes = 2ULL * sizeof(FrontierCell) * width;
    const auto required_bytes = frontier_bytes +
        2ULL * (target.seq.size() + candidate.seq.size()) + MEMORY_MARGIN;
    if (required_bytes > options.memory_limit) {
        throw std::runtime_error(
            "INCOMPLETE_RESOURCE: rolling memory budget exceeded required=" +
            std::to_string(required_bytes) + " limit=" +
            std::to_string(options.memory_limit)
        );
    }
    progress.required_memory = std::max<std::uint64_t>(
        progress.required_memory, required_bytes
    );
    progress.frontier_memory = std::max<std::uint64_t>(
        progress.frontier_memory, frontier_bytes
    );
    progress.job_index = job_index;
    progress.mode = mode;

    RollingCursor cursor;
    cursor.previous.resize(width);
    cursor.current.resize(width);
    prepare_current_row(cursor, plan, m);
    if (!options.checkpoint_directory.empty()) {
        const auto path = checkpoint_path(options, job_index, mode);
        if (std::filesystem::exists(path)) {
            CheckedReader reader(path);
            verify_checkpoint_identity(
                reader, options.checkpoint_identity, job_index, job,
                target, candidate, mode, plan
            );
            read_cursor(reader, cursor, plan, mode, n, m);
            reader.finish();
        }
    }
    progress.resumed_cells += cursor.processed_cells;
    std::uint64_t rows_since_checkpoint = 0;

    if (requested_signal != 0 && !options.checkpoint_directory.empty()) {
        save_checkpoint(
            options, job_index, job, target, candidate, mode, plan, cursor
        );
        progress.row = cursor.row;
        progress.next_column = cursor.next_column;
        write_progress(options, progress, "CHECKPOINTED");
        throw QuantumCheckpoint{};
    }

    while (cursor.row <= n) {
        if ((options.quantum != 0 &&
                progress.computed_cells >= options.quantum) ||
            (options.stop_after_rows != 0 &&
                progress.completed_rows >= options.stop_after_rows) ||
            requested_signal != 0) {
            if (options.checkpoint_directory.empty()) {
                throw std::runtime_error(
                    "interruption requires --checkpoint-dir for exact resume"
                );
            }
            save_checkpoint(
                options, job_index, job, target, candidate, mode, plan, cursor
            );
            progress.row = cursor.row;
            progress.next_column = cursor.next_column;
            write_progress(options, progress, "CHECKPOINTED");
            throw QuantumCheckpoint{};
        }

        evaluate_cell(cursor, target.seq, candidate.seq, mode);
        const int i = cursor.row;
        const int j = cursor.next_column;
        const auto& cell = cursor.current[j - cursor.current_low];
        if (i >= 1 && ((mode == Mode::TARGET_GLOBAL && i == n) ||
            (mode == Mode::CANDIDATE_GLOBAL && j == m))) {
            for (int state = 0; state < 3; ++state) {
                if (cell[state].score > NEG / 2 && better_endpoint(
                    cell[state].score, i, j, state, cursor.endpoint
                )) {
                    cursor.endpoint = {cell[state].score, i, j, state};
                    cursor.endpoint_path = cell[state];
                }
            }
        }
        ++cursor.processed_cells;
        ++progress.computed_cells;
        ++cursor.next_column;
        if (cursor.next_column > cursor.current_high) {
            cursor.previous.swap(cursor.current);
            cursor.previous_low = cursor.current_low;
            cursor.previous_high = cursor.current_high;
            ++cursor.row;
            ++progress.completed_rows;
            ++rows_since_checkpoint;
            if (cursor.row <= n) prepare_current_row(cursor, plan, m);
            else cursor.next_column = 0;
            if (rows_since_checkpoint >= options.checkpoint_rows ||
                cursor.row > n) {
                save_checkpoint(
                    options, job_index, job, target, candidate, mode, plan, cursor
                );
                rows_since_checkpoint = 0;
                progress.row = cursor.row;
                progress.next_column = cursor.next_column;
                write_progress(options, progress, "RUNNING");
            }
        }
    }
    if (cursor.processed_cells != plan.cells) {
        throw std::runtime_error("rolling evaluated cells differ from plan");
    }
    const auto& best = cursor.endpoint;
    const auto& path = cursor.endpoint_path;
    AlignResult result;
    result.valid = path.matches + path.mismatches > 0;
    result.score = best.score;
    result.target_start = path.target_start;
    result.candidate_start = path.candidate_start;
    result.target_end = best.i;
    result.candidate_end = best.j;
    result.matches = path.matches;
    result.mismatches = path.mismatches;
    result.gap_in_target = path.gap_in_target;
    result.gap_in_candidate = path.gap_in_candidate;
    result.gap_openings = path.gap_openings;
    result.columns = columns(path);
    result.cells = plan.cells;
    if (!result.valid || best.i < 0 || best.j < 0 ||
        (mode == Mode::TARGET_GLOBAL &&
            (result.target_start != 0 || result.target_end != n)) ||
        (mode == Mode::CANDIDATE_GLOBAL &&
            (result.candidate_start != 0 || result.candidate_end != m))) {
        throw std::runtime_error("certified rolling directional DP failed");
    }
    progress.row = cursor.row;
    progress.next_column = cursor.next_column;
    return result;
}

std::uint64_t strict_u64(const std::string& value, const std::string& option) {
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos) {
        throw std::runtime_error("invalid unsigned integer for " + option);
    }
    return parse_u64(value, option);
}

LinearOptions parse_linear_options(int argc, char** argv) {
    LinearOptions options;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        const auto next = [&]() -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error("missing value for " + argument);
            }
            return argv[++i];
        };
        if (argument == "--targets") options.targets = next();
        else if (argument == "--alignment-jobs") options.jobs = next();
        else if (argument == "--start-sequence-store") options.start_store = next();
        else if (argument == "--end-sequence-store") options.end_store = next();
        else if (argument == "--output") options.output = next();
        else if (argument == "--summary") options.summary = next();
        else if (argument == "--start-release-id") options.start_release = next();
        else if (argument == "--end-release-id") options.end_release = next();
        else if (argument == "--checkpoint-dir") options.checkpoint_directory = next();
        else if (argument == "--checkpoint-identity") options.checkpoint_identity = next();
        else if (argument == "--progress-json") options.progress_json = next();
        else if (argument == "--threads") {
            const auto threads = strict_u64(next(), argument);
            if (threads < 1 || threads > std::numeric_limits<int>::max()) {
                throw std::runtime_error("threads must be a positive int");
            }
            options.threads = static_cast<int>(threads);
        } else if (argument == "--checkpoint-rows") {
            options.checkpoint_rows = strict_u64(next(), argument);
        } else if (argument == "--memory-limit-bytes") {
            options.memory_limit = strict_u64(next(), argument);
        } else if (argument == "--max-total-cells") {
            options.quantum = strict_u64(next(), argument);
        } else if (argument == "--stop-after-rows") {
            options.stop_after_rows = strict_u64(next(), argument);
        } else {
            throw std::runtime_error("unknown argument: " + argument);
        }
    }
    if (options.targets.empty() || options.jobs.empty() ||
        options.start_store.empty() || options.end_store.empty() ||
        options.output.empty() || options.summary.empty() ||
        options.start_release.empty() || options.end_release.empty()) {
        throw std::runtime_error("missing required arguments");
    }
    if (options.start_release == options.end_release ||
        options.checkpoint_rows == 0 || options.memory_limit == 0) {
        throw std::runtime_error("invalid release/checkpoint/memory options");
    }
    if (!options.checkpoint_directory.empty()) {
        if (options.checkpoint_identity.size() != 64 ||
            options.checkpoint_identity.find_first_not_of("0123456789abcdef") !=
                std::string::npos) {
            throw std::runtime_error("checkpoint identity must be 64 lowercase hex");
        }
        if (options.progress_json.empty()) {
            options.progress_json = (
                std::filesystem::path(options.checkpoint_directory) / "progress.json"
            ).string();
        }
    } else if (options.quantum != 0 || options.stop_after_rows != 0 ||
        !options.checkpoint_identity.empty()) {
        throw std::runtime_error("quantum/stop/identity require --checkpoint-dir");
    }
    return options;
}

std::unordered_map<std::string, TargetRec> load_linear_targets(
    const std::string& path, const std::vector<Job>& jobs
) {
    std::unordered_set<std::string> required;
    for (const auto& job : jobs) required.insert(job.target_id);
    std::ifstream in(path);
    std::string line;
    if (!in || !std::getline(in, line)) {
        throw std::runtime_error("cannot read rolling targets");
    }
    const auto header = header_index(split_tab(line));
    const auto i_id = require_col(header, "target_id");
    const auto i_accession = require_col(header, "optional_accession");
    const auto i_sequence = require_col(header, "normalized_sequence");
    const auto last_column = std::max({i_id, i_accession, i_sequence});
    std::unordered_map<std::string, TargetRec> result;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        auto fields = split_tab(line);
        if (fields.size() <= last_column) {
            throw std::runtime_error("malformed rolling target row");
        }
        if (!required.count(fields[i_id])) continue;
        result.emplace(fields[i_id], TargetRec{
            std::move(fields[i_accession]), std::move(fields[i_sequence])
        });
    }
    if (!in.eof()) throw std::runtime_error("rolling targets read failed");
    return result;
}

std::unordered_map<std::uint64_t, CandidateRec> load_linear_store(
    const std::string& path,
    const std::string& release,
    const std::vector<Job>& jobs
) {
    std::unordered_set<std::uint64_t> required;
    for (const auto& job : jobs) {
        if (job.release == release) required.insert(job.rid);
    }
    std::ifstream in(path);
    std::string line;
    if (!in || !std::getline(in, line)) {
        throw std::runtime_error("cannot read rolling sequence store");
    }
    const auto header = header_index(split_tab(line));
    const auto i_release = require_col(header, "release");
    const auto i_rid = require_col(header, "rid");
    const auto i_accession = require_col(header, "primary_accession");
    const auto i_sequence = require_col(header, "sequence");
    const auto last_column = std::max({i_release, i_rid, i_accession, i_sequence});
    std::unordered_map<std::uint64_t, CandidateRec> result;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        auto fields = split_tab(line);
        if (fields.size() <= last_column || fields[i_release] != release) {
            throw std::runtime_error("malformed rolling sequence store row");
        }
        const auto rid = parse_u64(fields[i_rid], "RID");
        if (!required.count(rid)) continue;
        result.emplace(rid, CandidateRec{
            std::move(fields[i_accession]), std::move(fields[i_sequence])
        });
    }
    if (!in.eof()) throw std::runtime_error("rolling sequence store read failed");
    return result;
}

void write_alignment_header(std::ostream& out) {
    out << "release\ttarget_id\trid\tjob_scope\tdiscovery_channel"
        << "\ttarget_optional_accession\tcandidate_primary_accession"
        << "\ttarget_length\tcandidate_length";
    for (const std::string prefix : {"target_global", "candidate_global"}) {
        for (const std::string suffix : {
            "status", "score", "target_start0", "target_end0_exclusive",
            "candidate_start0", "candidate_end0_exclusive", "target_aligned_span",
            "candidate_aligned_span", "target_coverage", "candidate_coverage",
            "matches", "mismatches", "gap_in_target_residues",
            "gap_in_candidate_residues", "gap_openings", "aligned_residue_pairs",
            "alignment_columns", "identity_aligned_pairs",
            "identity_alignment_columns", "cells_evaluated"
        }) {
            out << '\t' << prefix << '_' << suffix;
        }
    }
    out << '\n';
}

void write_linear_outputs(
    const LinearOptions& options,
    const std::vector<std::string>& lines,
    std::uint64_t total_cells
) {
    const std::filesystem::path output_temporary(options.output + ".tmp");
    const std::filesystem::path summary_temporary(options.summary + ".tmp");
    std::ofstream out(output_temporary);
    if (!out) throw std::runtime_error("cannot create rolling output");
    write_alignment_header(out);
    for (const auto& line : lines) out << line << '\n';
    out.flush();
    if (!out) throw std::runtime_error("rolling output write failed");
    out.close();
    if (!out) throw std::runtime_error("rolling output close failed");
    std::ofstream summary(summary_temporary);
    if (!summary) throw std::runtime_error("cannot create rolling summary");
    summary << "metric\tvalue\n"
        << "job_count\t" << lines.size() << '\n'
        << "completed_count\t" << lines.size() << '\n'
        << "threads\t" << options.threads << '\n'
        << "directional_alignment_count\t" << lines.size() * 2ULL << '\n'
        << "total_dp_cells_evaluated\t" << total_cells << '\n'
        << "status_OK\t" << lines.size() * 2ULL << '\n';
    summary.flush();
    if (!summary) throw std::runtime_error("rolling summary write failed");
    summary.close();
    if (!summary) throw std::runtime_error("rolling summary close failed");
    install_atomic(output_temporary, options.output);
    install_atomic(summary_temporary, options.summary);
}

} // namespace

int main(int argc, char** argv) {
    try {
        const auto original_parent = ::getppid();
        if (argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << "probe_directional_glocal_linear_dp_v1\n";
            return 0;
        }
        const auto options = parse_linear_options(argc, argv);
        std::signal(SIGTERM, request_checkpoint);
        std::signal(SIGINT, request_checkpoint);
        if (!options.checkpoint_directory.empty()) {
            checkpoint_if_parent_dies(original_parent);
        }
        CheckpointLock lock(options.checkpoint_directory);
        const auto jobs = load_jobs(options.jobs, options.start_release, options.end_release);
        // Native large chunks are normally singleton jobs. Retain only records
        // required by this invocation, even when input TSVs cover the full run.
        const auto targets = load_linear_targets(options.targets, jobs);
        const auto start_store = load_linear_store(
            options.start_store, options.start_release, jobs
        );
        const auto end_store = load_linear_store(
            options.end_store, options.end_release, jobs
        );
        std::vector<std::string> lines;
        lines.reserve(jobs.size());
        std::uint64_t total_cells = 0;
        InvocationProgress progress;
        for (std::size_t index = 0; index < jobs.size(); ++index) {
            const auto& job = jobs[index];
            const auto& store = job.release == options.start_release
                ? start_store : end_store;
            const auto target_it = targets.find(job.target_id);
            const auto candidate_it = store.find(job.rid);
            if (target_it == targets.end() || candidate_it == store.end()) {
                throw std::runtime_error("target/candidate missing from rolling input");
            }
            const auto& target = target_it->second;
            const auto& candidate = candidate_it->second;
            const auto target_plan = plan_glocal(target.seq, candidate.seq, Mode::TARGET_GLOBAL);
            const auto candidate_plan = plan_glocal(target.seq, candidate.seq, Mode::CANDIDATE_GLOBAL);
            const auto pair_width = std::max(
                maximum_width(target_plan, static_cast<int>(candidate.seq.size())),
                maximum_width(candidate_plan, static_cast<int>(candidate.seq.size()))
            );
            const auto pair_memory = 2ULL * sizeof(FrontierCell) * pair_width +
                2ULL * (target.seq.size() + candidate.seq.size()) + MEMORY_MARGIN;
            if (pair_memory > options.memory_limit) {
                throw std::runtime_error(
                    "INCOMPLETE_RESOURCE: rolling memory budget exceeded required=" +
                    std::to_string(pair_memory) + " limit=" +
                    std::to_string(options.memory_limit)
                );
            }
            progress.required_memory = std::max<std::uint64_t>(
                progress.required_memory, pair_memory
            );
            progress.frontier_memory = std::max<std::uint64_t>(
                progress.frontier_memory,
                2ULL * sizeof(FrontierCell) * pair_width
            );
            const auto target_result = align_glocal_rolling(
                options, index, job, target, candidate,
                Mode::TARGET_GLOBAL, target_plan, progress
            );
            const auto candidate_result = align_glocal_rolling(
                options, index, job, target, candidate,
                Mode::CANDIDATE_GLOBAL, candidate_plan, progress
            );
            const auto pair_cells = target_plan.cells + candidate_plan.cells;
            if (total_cells > std::numeric_limits<std::uint64_t>::max() - pair_cells) {
                throw std::runtime_error("rolling total cell overflow");
            }
            total_cells += pair_cells;
            std::string line = job.release + '\t' + job.target_id + '\t' +
                std::to_string(job.rid) + '\t' + job.job_scope + '\t' +
                job.discovery_channel + '\t' + target.accession + '\t' +
                candidate.accession + '\t' + std::to_string(target.seq.size()) +
                '\t' + std::to_string(candidate.seq.size());
            append_result(line, target_result, target.seq.size(), candidate.seq.size());
            append_result(line, candidate_result, target.seq.size(), candidate.seq.size());
            lines.push_back(std::move(line));
        }
        if (progress.computed_cells + progress.resumed_cells != total_cells) {
            throw std::runtime_error("rolling invocation accounting mismatch");
        }
        write_linear_outputs(options, lines, total_cells);
        write_progress(options, progress, "COMPLETE");
        return 0;
    } catch (const QuantumCheckpoint&) {
        std::cerr << "CHECKPOINTED: exact rolling DP can resume\n";
        return 75;
    } catch (const std::exception& error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        return 2;
    }
}
