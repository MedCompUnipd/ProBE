cmake_minimum_required(VERSION 3.20)

project(probe_native_tools LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(ZLIB REQUIRED)
find_package(OpenMP REQUIRED)

add_executable(
    probe-release-fasta-v2
    src/probe_release_fasta_v2.cpp
)
target_link_libraries(
    probe-release-fasta-v2
    PRIVATE ZLIB::ZLIB
)

add_executable(
    probe-goa-normalize-v1
    src/probe_goa_normalize_v1.cpp
)

add_executable(
    probe-go-positive-filter-v1
    src/probe_go_positive_filter_v1.cpp
)

add_executable(
    probe-raw-ox-extract-v1
    src/probe_raw_ox_extract_v1.cpp
)

add_executable(
    probe-primary-candidate-scan-v1
    src/probe_primary_candidate_scan_v1.cpp
)

add_executable(
    probe-primary-candidate-scan-v2
    src/probe_primary_candidate_scan_v2.cpp
)

add_executable(
    probe-direct-accession-anchors-v1
    src/probe_direct_accession_anchors_v1.cpp
)

add_executable(
    probe-onehop-lineage-scan-v1
    src/probe_onehop_lineage_scan_v1.cpp
)

add_executable(
    probe-candidate-sequence-extract-v1
    src/probe_candidate_sequence_extract_v1.cpp
)

add_executable(
    probe-directional-glocal-full-dp-v1
    src/probe_directional_glocal_full_dp_v1.cpp
)
target_link_libraries(
    probe-directional-glocal-full-dp-v1
    PRIVATE OpenMP::OpenMP_CXX
)

add_executable(
    probe-full-exact-cross-strain-v1
    src/probe_full_exact_cross_strain_v1.cpp
)

# FIX1 canonical V2 certified bounded affine DP backend.
add_executable(
    probe-directional-glocal-bounded-dp-v2
    src/probe_directional_glocal_bounded_dp_v2.cpp
)
target_link_libraries(
    probe-directional-glocal-bounded-dp-v2
    PRIVATE OpenMP::OpenMP_CXX
)

# Exact bounded-memory continuation; the frozen bounded TU is included verbatim.
add_executable(
    probe-directional-glocal-linear-dp-v1
    src/probe_directional_glocal_linear_dp_v1.cpp
)
target_link_libraries(
    probe-directional-glocal-linear-dp-v1
    PRIVATE OpenMP::OpenMP_CXX
)
