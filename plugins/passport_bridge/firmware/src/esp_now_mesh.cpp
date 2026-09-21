#include "esp_now_mesh.h"

EspNowMesh espMesh;
static EspNowRecvCallback g_userRecvCb = nullptr;

static void onDataRecv(const uint8_t *mac, const uint8_t *data, int len) {
    if (g_userRecvCb) {
        g_userRecvCb(mac, data, len);
    }
}

EspNowMesh::EspNowMesh() : initialized(false) {
    memset(broadcastPeer, 0xFF, 6);
}

bool EspNowMesh::init() {
    if (esp_now_init() != ESP_OK) {
        log_e("ESP-NOW initialization failed!");
        return false;
    }

    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, broadcastPeer, 6);
    peerInfo.channel = 0;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        log_w("ESP-NOW failed to add broadcast peer");
    }

    esp_now_register_recv_cb(onDataRecv);
    initialized = true;
    log_i("ESP-NOW Peer-to-Peer Mesh initialized successfully.");
    return true;
}

bool EspNowMesh::sendBroadcast(const uint8_t* data, size_t len) {
    if (!initialized || len == 0 || len > 250) return false;
    esp_err_t result = esp_now_send(broadcastPeer, data, len);
    return (result == ESP_OK);
}

void EspNowMesh::setOnReceive(EspNowRecvCallback cb) {
    g_userRecvCb = cb;
}
