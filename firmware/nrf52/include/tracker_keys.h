#ifndef TRACKER_KEYS_H
#define TRACKER_KEYS_H

/* Paste the Apple Base64 advertisement string from Find_My_Web here. */
#define APPLE_ADVERTISEMENT_KEY_BASE64 ""

/* Paste the Google 40-character hexadecimal advertisement string here. */
#define GOOGLE_ADVERTISEMENT_KEY_HEX ""

/*
 * Radio configuration shared by both simultaneous advertising sets.
 *
 * Recommended balanced defaults:
 * - 2000 ms follows the Google Find Hub advertising recommendation.
 * - 0 dBm follows Google's minimum conducted transmit-power recommendation.
 */
#define ADVERTISING_INTERVAL_MS 2000U
#define ADVERTISING_TX_POWER_DBM 0

#endif
