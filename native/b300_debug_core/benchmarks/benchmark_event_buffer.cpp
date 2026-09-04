#include "b300/event_buffer.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>

int main() {
    constexpr std::size_t iterations = 250000;
    constexpr double minimum_events_per_second = 50000.0;

    b300::EventBuffer buffer(4096);
    const auto start = std::chrono::steady_clock::now();

    for (std::size_t index = 0; index < iterations; ++index) {
        buffer.push({
            static_cast<std::uint64_t>(index),
            1,
            static_cast<std::uint16_t>(index % 16),
            b300::TraceEventType::sample,
            static_cast<std::uint64_t>(index),
        });

        if ((index % 1024) == 1023) {
            (void)buffer.drain(1024);
        }
    }

    while (buffer.size() != 0) {
        (void)buffer.drain(4096);
    }

    const auto stop = std::chrono::steady_clock::now();
    const auto elapsed = std::chrono::duration<double>(stop - start).count();
    const auto rate = elapsed > 0.0 ? static_cast<double>(iterations) / elapsed : 0.0;

    std::cout << "events=" << iterations
              << " elapsed_s=" << elapsed
              << " events_per_second=" << rate
              << " dropped=" << buffer.dropped()
              << '\n';

    if (buffer.dropped() != 0) {
        std::cerr << "native benchmark unexpectedly dropped events\n";
        return EXIT_FAILURE;
    }

    if (rate < minimum_events_per_second) {
        std::cerr << "native throughput gate failed: " << rate
                  << " < " << minimum_events_per_second << " events/s\n";
        return EXIT_FAILURE;
    }

    return EXIT_SUCCESS;
}
