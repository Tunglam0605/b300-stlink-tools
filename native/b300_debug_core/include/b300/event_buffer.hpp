#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <vector>

#include "b300/trace_event.hpp"

namespace b300 {

class EventBuffer final {
public:
    explicit EventBuffer(std::size_t capacity);

    void push(const TraceEvent& event);
    std::vector<TraceEvent> drain(std::size_t max_items);

    std::size_t size() const;
    std::size_t capacity() const noexcept;
    std::uint64_t dropped() const;

private:
    const std::size_t capacity_;
    mutable std::mutex mutex_;
    std::deque<TraceEvent> queue_;
    std::uint64_t dropped_{0};
};

} // namespace b300
