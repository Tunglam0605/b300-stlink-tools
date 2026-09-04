cmake_minimum_required(VERSION 3.20)
project(b300_debug_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(b300_debug_core STATIC
    src/sample_buffer.cpp
    src/event_buffer.cpp
    src/trace_decoder.cpp
    src/trace_pipeline.cpp
)

target_include_directories(b300_debug_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(b300_debug_core PUBLIC cxx_std_17)
set_target_properties(b300_debug_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

option(B300_NATIVE_BUILD_PYTHON "Build optional pybind11 Python module" OFF)
if(B300_NATIVE_BUILD_PYTHON)
    set(PYBIND11_FINDPYTHON ON)
    find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
    find_package(pybind11 CONFIG REQUIRED)
    pybind11_add_module(_b300_debug_core bindings/python/module.cpp)
    target_link_libraries(_b300_debug_core PRIVATE b300_debug_core)
    target_compile_features(_b300_debug_core PRIVATE cxx_std_17)
endif()

option(B300_NATIVE_BUILD_TESTS "Build native debug-core tests" ON)
if(B300_NATIVE_BUILD_TESTS)
    enable_testing()

    add_executable(b300_sample_buffer_tests tests/test_sample_buffer.cpp)
    target_link_libraries(b300_sample_buffer_tests PRIVATE b300_debug_core)
    add_test(NAME b300_sample_buffer_tests COMMAND b300_sample_buffer_tests)

    add_executable(b300_event_buffer_tests tests/test_event_buffer.cpp)
    target_link_libraries(b300_event_buffer_tests PRIVATE b300_debug_core)
    add_test(NAME b300_event_buffer_tests COMMAND b300_event_buffer_tests)

    add_executable(b300_trace_decoder_tests tests/test_trace_decoder.cpp)
    target_link_libraries(b300_trace_decoder_tests PRIVATE b300_debug_core)
    add_test(NAME b300_trace_decoder_tests COMMAND b300_trace_decoder_tests)

    add_executable(b300_trace_pipeline_tests tests/test_trace_pipeline.cpp)
    target_link_libraries(b300_trace_pipeline_tests PRIVATE b300_debug_core)
    add_test(NAME b300_trace_pipeline_tests COMMAND b300_trace_pipeline_tests)

    add_executable(b300_event_buffer_benchmark benchmarks/benchmark_event_buffer.cpp)
    target_link_libraries(b300_event_buffer_benchmark PRIVATE b300_debug_core)
    add_test(NAME b300_event_buffer_benchmark COMMAND b300_event_buffer_benchmark)
endif()
