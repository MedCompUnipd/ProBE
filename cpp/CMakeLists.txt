cmake_minimum_required(VERSION 3.20)

project(probe_release_fasta_v2 LANGUAGES CXX)

find_package(ZLIB REQUIRED)

add_executable(probe-release-fasta-v2 src/probe_release_fasta_v2.cpp)
target_compile_features(probe-release-fasta-v2 PRIVATE cxx_std_17)
target_link_libraries(probe-release-fasta-v2 PRIVATE ZLIB::ZLIB)
