#include <stdint.h>

typedef enum {
    MODE_IDLE = 0,
    MODE_RUN = 3
} Mode;

typedef struct {
    int16_t x;
    int16_t y;
} Position;

typedef union {
    uint32_t raw;
    float volts;
} SensorValue;

typedef struct {
    Position position;
    uint16_t rpm[2];
    SensorValue sensor;
    Mode mode;
    unsigned flags : 3;
    const volatile uint8_t status;
    uint8_t matrix[2][3];
    uint32_t *next;
} MachineState;

MachineState g_machine __attribute__((at(0x20000000)));
static uint32_t duplicate_static;
volatile uint32_t g_counter;

void Reset_Handler(void) {
    g_counter += duplicate_static;
}
