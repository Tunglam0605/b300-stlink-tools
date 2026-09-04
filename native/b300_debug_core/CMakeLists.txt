cmake_minimum_required(VERSION 3.20)
project(b300_debug_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(b300_debug_core STATIC
    src/sample_buffer.cpp
)

target_include_directories(b300_debug_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(b300_debug_core PUBLIC cxx_std_17)

option(B300_NATIVE_BUILD_TESTS "Build native debug-core tests" ON)
if(B300_NATIVE_BUILD_TESTS)
    enable_testing()
    add_executable(b300_debug_core_tests
        tests/test_sample_buffer.cpp
    )
    target_link_libraries(b300_debug_core_tests PRIVATE b300_debug_core)
    add_test(NAME b300_debug_core_tests COMMAND b300_debug_core_tests)
endif()
