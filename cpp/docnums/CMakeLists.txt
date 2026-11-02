cmake_minimum_required(VERSION 3.16)
project(docnums LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(docnums main.cpp)

if(MSVC)
  target_compile_options(docnums PRIVATE /W4)
else()
  target_compile_options(docnums PRIVATE -Wall -Wextra)
  # 单文件产物：不依赖 libstdc++/libgcc DLL（否则换台机器报 0xC0000135）
  target_link_options(docnums PRIVATE -static)
  if(WIN32)
    # CommandLineToArgvW：Windows 的 argv 是 ANSI，中文路径会炸
    target_link_libraries(docnums PRIVATE shell32)
  endif()
endif()

enable_testing()
find_package(Python3 COMPONENTS Interpreter)
if(Python3_FOUND)
  add_test(NAME parity
           COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/parity.py
                   $<TARGET_FILE:docnums>)
  set_tests_properties(parity PROPERTIES
                       WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
endif()
