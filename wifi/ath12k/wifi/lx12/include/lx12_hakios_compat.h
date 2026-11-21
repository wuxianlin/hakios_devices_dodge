/* HakiOS compatibility: the wifi/lx sources were written against the redwood-named
 * station ABI; this tree's bsd/sys/hakios.sta.h is the same ABI under hakios names. */
#ifndef _LX12_HAKIOS_COMPAT_H
#define _LX12_HAKIOS_COMPAT_H
#define REDWOOD_STA_ELEMENTS_MOST HAKIOS_STA_ELEMENTS_MOST
#define REDWOOD_STA_OPEN          HAKIOS_STA_OPEN
#define REDWOOD_STA_WPA2_PSK      HAKIOS_STA_WPA2_PSK
#define REDWOOD_STA_JOINED        HAKIOS_STA_JOINED
#define REDWOOD_STA_JOINING       HAKIOS_STA_JOINING
#define REDWOOD_STA_SCANNING      HAKIOS_STA_SCANNING
#define REDWOOD_STA_IDLE          HAKIOS_STA_IDLE
#define redwood_sta_join          hakios_sta_join
#define redwood_sta_scan          hakios_sta_scan
#define redwood_sta_bss           hakios_sta_bss
#define redwood_sta_channel       hakios_sta_channel
#define redwood_sta_ops           hakios_sta_ops
#define redwood_sta_state         hakios_sta_state
#define REDWOOD_STA_CHANNEL_PASSIVE HAKIOS_STA_CHANNEL_PASSIVE
#define REDWOOD_STA_CHANNEL_RADAR   HAKIOS_STA_CHANNEL_RADAR
#endif
