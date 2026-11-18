cmake_minimum_required(VERSION 3.25)

project(ColdPath LANGUAGES CXX)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(FATAL_ERROR "Out-of-tree builds only: cmake -B build")
endif()
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
# No LTO: the handler must stay a separate call, as in the measured code.
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION OFF)
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O2 -g -DNDEBUG")

find_package(OpenSSL REQUIRED)
find_package(ZLIB REQUIRED)
find_package(nlohmann_json 3 REQUIRED)

set(THIRD_PARTY ${CMAKE_SOURCE_DIR}/third_party)
add_library(cpptrader STATIC
    ${THIRD_PARTY}/CppTrader/source/trader/matching/market_manager.cpp
    ${THIRD_PARTY}/CppTrader/source/trader/matching/order.cpp
    ${THIRD_PARTY}/CppTrader/source/trader/matching/order_book.cpp
    ${THIRD_PARTY}/CppTrader/source/trader/providers/nasdaq/itch_handler.cpp
)
target_include_directories(cpptrader SYSTEM PUBLIC
    ${THIRD_PARTY}/CppTrader/include
    ${THIRD_PARTY}/CppCommon/include
    ${THIRD_PARTY}/fmt/include
)
set_target_properties(cpptrader PROPERTIES CXX_STANDARD 20)

add_library(coldpath_common STATIC
    src/common/args.cpp
    src/common/itch.cpp
    src/common/process.cpp
)
target_include_directories(coldpath_common PUBLIC src)
target_compile_options(coldpath_common PUBLIC -Wall -Wextra)

add_library(handler_cpptrader STATIC src/handler/cpptrader.cpp)
target_link_libraries(handler_cpptrader PUBLIC coldpath_common cpptrader)
add_library(handler_synth STATIC src/handler/synth.cpp)
target_link_libraries(handler_synth PUBLIC coldpath_common)
add_library(handler_null STATIC src/handler/null.cpp)
target_link_libraries(handler_null PUBLIC coldpath_common)

# bench uses CppTrader; bench-synth and bench-null use the synthetic model and the empty handler.
foreach(variant IN ITEMS cpptrader synth null)
    if(variant STREQUAL "cpptrader")
        set(name bench)
    else()
        set(name bench-${variant})
    endif()
    add_executable(${name} src/bench/main.cpp src/bench/counters.cpp src/bench/disrupt.cpp src/bench/prefetch.cpp)
    target_link_libraries(${name} PRIVATE handler_${variant})
endforeach()

add_executable(recv src/recv/main.cpp src/recv/journal.cpp src/recv/socket.cpp)
target_link_libraries(recv PRIVATE handler_cpptrader)

add_executable(feed
    src/feed/common.cpp
    src/feed/live.cpp
    src/feed/main.cpp
    src/feed/recording.cpp
    src/feed/replay.cpp
    src/feed/sender.cpp
    src/feed/synth.cpp
    src/feed/translator.cpp
    src/feed/websocket.cpp
)
target_link_libraries(feed PRIVATE coldpath_common OpenSSL::SSL OpenSSL::Crypto ZLIB::ZLIB nlohmann_json::nlohmann_json)

# Handler size axis: CppTrader + generated code (KB), model rows, order sending (0/1).
# Each variant gives handler_<name>, bench-<name> and recv-<name>; SEED changes the generated code layout.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(COLDPATH_GEN_DIR ${CMAKE_BINARY_DIR}/gen)

function(coldpath_code kb seed)
    set(target coldpath_code_${kb}_s${seed})
    if(TARGET ${target})
        return()
    endif()
    set(out ${COLDPATH_GEN_DIR}/code_${kb}_s${seed}.cpp)
    add_custom_command(OUTPUT ${out}
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/gen-code.py ${kb} --seed ${seed} -o ${out}
        DEPENDS ${CMAKE_SOURCE_DIR}/gen-code.py
        COMMENT "gen-code.py: ${kb} KB, seed ${seed}"
        VERBATIM)
    add_library(${target} STATIC ${out})
    # Functions in source order, each on its own 128-byte slot.
    target_compile_options(${target} PRIVATE -falign-functions=64 $<$<CXX_COMPILER_ID:GNU>:-fno-toplevel-reorder>)
endfunction()

function(coldpath_variant name code_kb data_lines send)
    cmake_parse_arguments(PARSE_ARGV 4 ARG "" "SEED" "")
    if(NOT ARG_SEED)
        set(ARG_SEED 1)
    endif()
    add_library(handler_${name} STATIC src/handler/cpptrader.cpp src/handler/load.cpp src/handler/send.cpp)
    target_link_libraries(handler_${name} PUBLIC coldpath_common cpptrader)
    target_compile_definitions(handler_${name} PRIVATE
        COLDPATH_CODE_KB=${code_kb} COLDPATH_DATA_LINES=${data_lines} COLDPATH_SEND=${send})
    if(code_kb GREATER 0)
        coldpath_code(${code_kb} ${ARG_SEED})
        target_link_libraries(handler_${name} PUBLIC coldpath_code_${code_kb}_s${ARG_SEED})
    endif()
    add_executable(bench-${name} src/bench/main.cpp src/bench/counters.cpp src/bench/disrupt.cpp src/bench/prefetch.cpp)
    target_link_libraries(bench-${name} PRIVATE handler_${name})
    add_executable(recv-${name} src/recv/main.cpp src/recv/journal.cpp src/recv/socket.cpp)
    target_link_libraries(recv-${name} PRIVATE handler_${name})
endfunction()

coldpath_variant(tx 0 0 1)
coldpath_variant(c32 32 0 0)
coldpath_variant(c128 128 0 0)
coldpath_variant(big 128 1024 1)
coldpath_variant(c512 512 0 0)

# Background load for the isolation series; its code has its own seed, unlike any handler's.
coldpath_code(128 9)
add_executable(neighbour src/neighbour/main.cpp)
target_link_libraries(neighbour PRIVATE coldpath_common coldpath_code_128_s9)

# Synthetic data points: dependent reads (d64, d512) and the independent control (d64i), with PrefetchData.
function(coldpath_synth name chain lines table_log2)
    add_library(handler_${name} STATIC src/handler/synth.cpp)
    target_link_libraries(handler_${name} PUBLIC coldpath_common)
    target_compile_definitions(handler_${name} PRIVATE COLDPATH_SYNTH_CHAIN=${chain} COLDPATH_SYNTH_TOUCH=${lines}
        COLDPATH_SYNTH_TABLE_LOG2=${table_log2} COLDPATH_SYNTH_PREFETCH=1)
    add_executable(bench-${name} src/bench/main.cpp src/bench/counters.cpp src/bench/disrupt.cpp src/bench/prefetch.cpp)
    target_link_libraries(bench-${name} PRIVATE handler_${name})
endfunction()

coldpath_synth(d64 1 1024 14)
coldpath_synth(d64i 0 1024 14)
coldpath_synth(d512 1 8192 18)
