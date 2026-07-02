/*
 * Low-power Apple Find My + Google Find Hub broadcaster for nRF52832.
 *
 * Both providers use controller-managed, non-connectable advertising sets.
 * The CPU sleeps forever after startup; no periodic application task is used.
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <bluetooth/bluetooth.h>
#include <bluetooth/hci.h>
#include <bluetooth/hci_vs.h>
#include <kernel.h>
#include <sys/byteorder.h>
#include <sys/util.h>

#include "tracker_keys.h"

#define ADV_INTERVAL_UNITS ((ADVERTISING_INTERVAL_MS * 8U) / 5U)

BUILD_ASSERT(
    sizeof(APPLE_ADVERTISEMENT_KEY_BASE64) == 1 ||
        sizeof(APPLE_ADVERTISEMENT_KEY_BASE64) == 41,
    "Apple key must be empty or exactly 40 Base64 characters"
);
BUILD_ASSERT(
    sizeof(GOOGLE_ADVERTISEMENT_KEY_HEX) == 1 ||
        sizeof(GOOGLE_ADVERTISEMENT_KEY_HEX) == 41,
    "Google advertisement key must be empty or exactly 40 hexadecimal characters"
);
BUILD_ASSERT(
    CONFIG_BT_EXT_ADV_MAX_ADV_SET >= 2,
    "Apple and Google require two simultaneous advertising sets"
);
BUILD_ASSERT(
    ADVERTISING_INTERVAL_MS >= 20U && ADVERTISING_INTERVAL_MS <= 10240U,
    "Advertising interval must be between 20 and 10240 milliseconds"
);
BUILD_ASSERT(
    ADVERTISING_TX_POWER_DBM >= -40 && ADVERTISING_TX_POWER_DBM <= 4,
    "nRF52832 transmit power must be between -40 and +4 dBm"
);

static uint8_t apple_key[28];
static uint8_t apple_mfg_data[29] = {
    0x4c, 0x00, /* Apple company ID */
    0x12, 0x19, /* Offline Finding frame type and length */
    0x00,       /* Status */
    /* Bytes 5..26: key bytes 6..27 */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0x00,       /* key[0] >> 6 */
    0x00,       /* Hint */
};

static uint8_t google_service_data[24] = {
    0xaa, 0xfe, /* Service UUID 0xFEAA, little endian */
    0x41,       /* Find Hub frame with unwanted-tracking indication */
    /* Bytes 3..22: 20-byte advertisement EID */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0x00,       /* Hashed flags: battery indication unsupported */
};

static const uint8_t google_flags = BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR;

static struct bt_data apple_ad[] = {
    BT_DATA(BT_DATA_MANUFACTURER_DATA, apple_mfg_data, sizeof(apple_mfg_data)),
};

static struct bt_data google_ad[] = {
    BT_DATA(BT_DATA_FLAGS, &google_flags, sizeof(google_flags)),
    BT_DATA(BT_DATA_SVC_DATA16, google_service_data, sizeof(google_service_data)),
};

static struct bt_le_ext_adv *apple_advertiser;
static struct bt_le_ext_adv *google_advertiser;

static int set_advertising_tx_power(struct bt_le_ext_adv *advertiser)
{
    struct bt_hci_cp_vs_write_tx_power_level *command;
    struct net_buf *buffer;
    struct net_buf *response = NULL;

    buffer = bt_hci_cmd_create(
        BT_HCI_OP_VS_WRITE_TX_POWER_LEVEL, sizeof(*command)
    );
    if (buffer == NULL) return -ENOMEM;

    command = net_buf_add(buffer, sizeof(*command));
    command->handle_type = BT_HCI_VS_LL_HANDLE_TYPE_ADV;
    command->handle = sys_cpu_to_le16(bt_le_ext_adv_get_index(advertiser));
    command->tx_power_level = ADVERTISING_TX_POWER_DBM;

    int error = bt_hci_cmd_send_sync(
        BT_HCI_OP_VS_WRITE_TX_POWER_LEVEL, buffer, &response
    );
    if (response != NULL) net_buf_unref(response);
    return error;
}

static int base64_value(char value)
{
    if (value >= 'A' && value <= 'Z') return value - 'A';
    if (value >= 'a' && value <= 'z') return value - 'a' + 26;
    if (value >= '0' && value <= '9') return value - '0' + 52;
    if (value == '+') return 62;
    if (value == '/') return 63;
    return -1;
}

static bool decode_apple_key(void)
{
    const char *input = APPLE_ADVERTISEMENT_KEY_BASE64;
    if (input[0] == '\0') return false;

    uint32_t accumulator = 0;
    unsigned int bits = 0;
    size_t output_length = 0;

    for (size_t i = 0; input[i] != '\0'; ++i) {
        if (input[i] == '=') break;
        int value = base64_value(input[i]);
        if (value < 0) return false;
        accumulator = (accumulator << 6) | (uint32_t)value;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            if (output_length >= sizeof(apple_key)) return false;
            apple_key[output_length++] = (uint8_t)(accumulator >> bits);
            accumulator &= (1U << bits) - 1U;
        }
    }
    return output_length == sizeof(apple_key);
}

static int hex_value(char value)
{
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

static bool decode_google_eid(void)
{
    const char *input = GOOGLE_ADVERTISEMENT_KEY_HEX;
    if (input[0] == '\0') return false;

    for (size_t i = 0; i < 20; ++i) {
        int high = hex_value(input[i * 2]);
        int low = hex_value(input[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        google_service_data[3 + i] = (uint8_t)((high << 4) | low);
    }
    return true;
}

static int start_apple_beacon(void)
{
    memcpy(&apple_mfg_data[5], &apple_key[6], 22);
    apple_mfg_data[27] = apple_key[0] >> 6;

    bt_addr_le_t address = { .type = BT_ADDR_LE_RANDOM };
    address.a.val[5] = apple_key[0] | 0xc0;
    address.a.val[4] = apple_key[1];
    address.a.val[3] = apple_key[2];
    address.a.val[2] = apple_key[3];
    address.a.val[1] = apple_key[4];
    address.a.val[0] = apple_key[5];

    int identity = bt_id_create(&address, NULL);
    if (identity < 0) return identity;

    struct bt_le_adv_param params = BT_LE_ADV_PARAM_INIT(
        BT_LE_ADV_OPT_USE_IDENTITY,
        ADV_INTERVAL_UNITS,
        ADV_INTERVAL_UNITS,
        NULL
    );
    params.id = (uint8_t)identity;

    int error = bt_le_ext_adv_create(&params, NULL, &apple_advertiser);
    if (error) return error;
    error = bt_le_ext_adv_set_data(
        apple_advertiser, apple_ad, ARRAY_SIZE(apple_ad), NULL, 0
    );
    if (error) return error;
    error = set_advertising_tx_power(apple_advertiser);
    if (error) return error;
    return bt_le_ext_adv_start(
        apple_advertiser, BT_LE_EXT_ADV_START_DEFAULT
    );
}

static int start_google_beacon(void)
{
    struct bt_le_adv_param params = BT_LE_ADV_PARAM_INIT(
        BT_LE_ADV_OPT_USE_IDENTITY,
        ADV_INTERVAL_UNITS,
        ADV_INTERVAL_UNITS,
        NULL
    );
    params.id = BT_ID_DEFAULT;

    int error = bt_le_ext_adv_create(&params, NULL, &google_advertiser);
    if (error) return error;
    error = bt_le_ext_adv_set_data(
        google_advertiser, google_ad, ARRAY_SIZE(google_ad), NULL, 0
    );
    if (error) return error;
    error = set_advertising_tx_power(google_advertiser);
    if (error) return error;
    return bt_le_ext_adv_start(
        google_advertiser, BT_LE_EXT_ADV_START_DEFAULT
    );
}

int main(void)
{
    bool apple_enabled = decode_apple_key();
    bool google_enabled = decode_google_eid();

    if (bt_enable(NULL) != 0) return 0;

    int apple_error = apple_enabled ? start_apple_beacon() : 0;
    int google_error = google_enabled ? start_google_beacon() : 0;

    if (apple_error != 0 || google_error != 0) return 0;

    /*
     * Both controller advertising sets remain active concurrently from here.
     * The application CPU has no switching loop and can sleep indefinitely.
     */
    k_sleep(K_FOREVER);
    return 0;
}
