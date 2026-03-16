cmake_minimum_required(VERSION 3.18)
project(cpp_hal LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find Python first (required by nanobind)
find_package(Python 3.10 COMPONENTS Interpreter Development REQUIRED)

# Find nanobind via its CMake config
execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_VARIABLE NB_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY
)
list(APPEND CMAKE_PREFIX_PATH "${NB_DIR}")
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(cpp_hal
    bindings.cpp
    mock_frame_sensor.cpp
    mock_video_sensor.cpp
    sensor_factory.cpp
)

target_include_directories(cpp_hal PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
