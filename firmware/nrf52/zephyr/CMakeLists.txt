cmake_minimum_required(VERSION 3.13.1)
include($ENV{ZEPHYR_BASE}/cmake/app/boilerplate.cmake NO_POLICY_SCOPE)
project(nrf52_apple_google_low_power)

target_include_directories(app PRIVATE ../include)
target_sources(app PRIVATE ../src/main.c)
