#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <vector>

namespace b300 {

struct SamplePoint {
    std::uint64_t timestamp_ns{};
    std::uint32_t channel_id{};
    std::uint64_t raw_value{};
};

class SampleBuffer final {
public:
    explicit SampleBuffer(std::size_t capacity);

    void push(const SamplePoint& sample);
    std::vector<SamplePoint> drain(std::size_t max_items);

    std::size_t size() const;
    std::size_t capacity() const noexcept;
    std::uint64_t dropped() const noexcept;

private:
    const std::size_t capacity_;
    mutable std::mutex mutex_;
    std::deque<SamplePoint> queue_;
    std::uint64_t dropped_{0};
};

} // namespace b300
