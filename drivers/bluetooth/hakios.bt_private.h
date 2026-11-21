/*
 * What the Bluetooth files share. hakios_bt_uart.c drives SE14, the 4-wire UART to the WCN7850's Bluetooth
 * half; hakios_bt_hci.c brings the chip up over it (boot-arg bt=N). The UART side moves H4 bytes and knows
 * nothing of HCI, so a later consumer (bluetoothd's channel) can take the chip over through the same calls.
 */
#ifndef HAKIOS_BT_PRIVATE_H
#define HAKIOS_BT_PRIVATE_H

#include <stdbool.h>
#include <stdint.h>

/* their mhi_pcie_say went to the loader's log too; ours prints the same way */
static inline void bt_say(const char *format, ...) __attribute__((format(printf, 1, 2)));

#define BT_BAUD                 115200u
#define BT_BAUD_HIGH            3000000u

/* clocks, pins and the engine's own checks (protocol, FIFO interface); false after saying why */
bool hakios_bt_uart_open(void);
/* 8N1, RTS/CTS flow control, BT_BAUD at that oversampling (16 or 32); the receiver started, the buffer emptied */
bool hakios_bt_uart_config(uint32_t oversampling);
/* dynamic baud rate switch (115200 <-> 3000000): reconfigures S6 RCG clock & GENI dividers */
bool hakios_bt_uart_set_baud(uint32_t baud);
uint32_t hakios_bt_uart_get_baud(void);
/* waits until the engine has sent every byte (the chip's CTS may hold it); the RX FIFO is drained meanwhile */
bool hakios_bt_uart_write(const uint8_t *data, uint32_t length);
/* the RX FIFO into the receive buffer, whole words while there is room; how many bytes came */
uint32_t hakios_bt_uart_poll(void);
uint32_t hakios_bt_uart_drain(void);
void hakios_bt_uart_discard_rx(bool on);
void hakios_bt_uart_irq_clear(void);
/* the received bytes nobody has taken yet, oldest first; valid until the next poll or take */
const uint8_t *hakios_bt_uart_rx(uint32_t *length);
void hakios_bt_uart_take(uint32_t length);
void hakios_bt_uart_status(const char *when);
uint32_t hakios_bt_uart_fifo_status(void);
void hakios_bt_hex(const char *what, const uint8_t *data, uint32_t length);

/* WCN7850 vendor command 0xFC48 baud rate transition sequence */
bool hakios_bt_hci_set_baudrate(uint32_t baud);

/* bt=4 (hakios_bt_ios.c): /dev/uart.bluetooth, com.apple.uart.sk.bluetooth and the Skywalk nexus for bluetoothd */
void hakios_bt_ios_early(void);
void hakios_bt_ios_serve(const uint8_t addr[6]);
void hakios_bt_uart_isr(void *ctx, uint32_t intid);

#endif

#include <stdarg.h>
extern void bt_say_v(const char *format, va_list ap);
static inline void
bt_say(const char *format, ...)
{
	va_list ap;

	va_start(ap, format);
	bt_say_v(format, ap);
	va_end(ap);
}

/* their mhi_pcie_private.h's MMIO pair, verbatim */
static inline uint32_t
mhi_pcie_rd(vm_offset_t address)
{
	return *(volatile uint32_t *)address;
}

static inline void
mhi_pcie_wr(vm_offset_t address, uint32_t value)
{
	*(volatile uint32_t *)address = value;
}
