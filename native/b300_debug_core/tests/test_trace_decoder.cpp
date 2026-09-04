#include "b300/trace_decoder.hpp"

#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <vector>

int main() {
    b300::FixedWidthValueDecoder decoder(7);
    const std::uint8_t bytes[] = {
        0x78, 0x56, 0x34, 0x12,
        0xEF, 0xCD, 0xAB, 0x90,
        0xAA,
    };

    std::vector<b300::TraceEvent> events;
    const auto consumed = decoder.decode(
        bytes,
        sizeof(bytes),
        b300::DecodeContext{1000, 42},
        events
    );

    assert(consumed == 8);
    assert(events.size() == 2);
    assert(events[0].timestamp_ns == 1000);
    assert(events[0].source_id == 42);
    assert(events[0].channel == 7);
    assert(events[0].type == b300::TraceEventType::sample);
    assert(events[0].value == 0x12345678ULL);
    assert(events[1].timestamp_ns == 1001);
    assert(events[1].value == 0x90ABCDEFULL);

    bool threw = false;
    try {
        decoder.decode(nullptr, 4, b300::DecodeContext{}, events);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    assert(threw);

    return 0;
}
