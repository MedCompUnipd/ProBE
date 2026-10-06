cmake_minimum_required(VERSION 3.20)

project(probe_native_tools LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(ZLIB REQUIRED)

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
