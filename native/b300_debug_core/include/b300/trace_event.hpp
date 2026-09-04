#pragma once

#include <cstdint>

namespace b300 {

enum class TraceEventType : std::uint8_t {
    unknown = 0,
    itm_data = 1,
    text = 2,
    task_switch = 3,
    isr_enter = 4,
    isr_exit = 5,
    sample = 6,
};

struct TraceEvent {
    std::uint64_t timestamp_ns{};
    std::uint32_t source_id{};
    std::uint16_t channel{};
    TraceEventType type{TraceEventType::unknown};
    std::uint64_t value{};
};

} // namespace b300
