#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <MPU6050_light.h>
#include <PubSubClient.h>
#include <esp_bt.h>

MPU6050 mpu(Wire);

// ============================================================
// Hardware Pin Configuration
// ============================================================

const int BUTTON_CANCEL_PIN = 4;   // Push button between GPIO 4 and GND
const int LED_PIN           = 25;  // LED output
const int BUZZER_PIN        = 26;  // Active buzzer output

// ============================================================
// Wi-Fi Configuration
// ============================================================

const char* WIFI_SSID     = "Accident-Pi";
const char* WIFI_PASSWORD = "12345678";

// ============================================================
// Raspberry Pi MQTT Configuration
// ============================================================

const char* MQTT_BROKER = "10.42.0.1";
const int   MQTT_PORT   = 1883;

const char* DEVICE_ID = "c1";

const char* MQTT_EVENT_TOPIC   = "accident/c1/event";
const char* MQTT_CANCEL_TOPIC  = "accident/c1/cancel";
const char* MQTT_COMMAND_TOPIC = "accident/c1/command";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ============================================================
// Vehicle Information
// ============================================================

const float TEST_LATITUDE  = 11.2588;
const float TEST_LONGITUDE = 75.7804;

// ============================================================
// Accident Detection Thresholds
// ============================================================

const float XY_IMPACT_THRESH        = 12.0;  // m/s^2
const float ACCIDENT_DELTA_V_THRESH = 1.2;   // m/s
const float TILT_ANGLE_THRESH       = 55.0;  // degrees
const float LOW_Z_FREEFALL_THRESH   = 4.0;   // m/s^2
const float DECAY_FACTOR            = 0.80;

// ============================================================
// Timing & System State
// ============================================================

unsigned long previousTime = 0;
unsigned long lastAccidentTime = 0;

const unsigned long ALERT_DURATION_MS = 10000; // 10 seconds

// ============================================================
// LED / Buzzer Pulsing
// ============================================================

unsigned long lastBlinkTime = 0;

const unsigned long BLINK_INTERVAL_MS = 100;

bool alertOutputState = false;

// ============================================================
// Accident State
// ============================================================

float deltaV = 0.0;

bool accidentTriggered = false;

// ============================================================
// Button Debounce
// ============================================================

int lastButtonReading = HIGH;
int verifiedButtonState = HIGH;

unsigned long lastDebounceTime = 0;

const unsigned long DEBOUNCE_DELAY_MS = 50;

// ============================================================
// Wi-Fi Reconnection
// ============================================================

void checkAndReconnectWiFi()
{
    if (WiFi.status() == WL_CONNECTED)
    {
        return;
    }

    static unsigned long lastAttemptTime = 0;

    unsigned long now = millis();

    if (now - lastAttemptTime > 10000)
    {
        lastAttemptTime = now;

        Serial.println("\n[Wi-Fi] Attempting connection...");

        WiFi.mode(WIFI_STA);
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
}

// ============================================================
// MQTT Reconnection
// ============================================================

void reconnectMQTT()
{
    if (mqttClient.connected())
    {
        return;
    }

    static unsigned long lastMQTTAttempt = 0;

    unsigned long now = millis();

    if (now - lastMQTTAttempt < 5000)
    {
        return;
    }

    lastMQTTAttempt = now;

    Serial.println("[MQTT] Attempting connection...");

    String clientID = "ESP32-" + String(DEVICE_ID);

    if (mqttClient.connect(clientID.c_str()))
    {
        Serial.println("[MQTT] Connected to Raspberry Pi!");

        mqttClient.subscribe(MQTT_COMMAND_TOPIC);

        Serial.print("[MQTT] Subscribed to: ");
        Serial.println(MQTT_COMMAND_TOPIC);
    }
    else
    {
        Serial.print("[MQTT] Connection failed. State: ");
        Serial.println(mqttClient.state());
    }
}

// ============================================================
// Turn OFF LED and Buzzer
// ============================================================

void turnOffAlertOutputs()
{
    alertOutputState = false;

    digitalWrite(LED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
}

// ============================================================
// MQTT Command Callback
// ============================================================

void mqttCallback(char* topic, byte* payload, unsigned int length)
{
    String message = "";

    for (unsigned int i = 0; i < length; i++)
    {
        message += (char)payload[i];
    }

    Serial.println("\n=========================================");
    Serial.println("[MQTT] MESSAGE RECEIVED");
    Serial.print("[MQTT] Topic: ");
    Serial.println(topic);
    Serial.print("[MQTT] Message: ");
    Serial.println(message);
    Serial.println("=========================================");

    // --------------------------------------------------------
    // APP CANCELLATION COMMAND
    // --------------------------------------------------------

    if (String(topic) == MQTT_COMMAND_TOPIC)
    {
        // IMPORTANT:
        // We check for CANCEL anywhere in the JSON message.
        //
        // This works for both:
        //
        // {"command":"CANCEL"}
        //
        // and
        //
        // {"command": "CANCEL"}
        //

        if (message.indexOf("CANCEL") >= 0)
        {
            Serial.println("\n🛑 APP CANCELLATION RECEIVED!");
            Serial.println("Turning OFF LED and Buzzer...");

            // Immediately stop physical alert
            turnOffAlertOutputs();

            // Clear accident state
            accidentTriggered = false;
            deltaV = 0.0;

            Serial.println("✅ LED OFF");
            Serial.println("✅ BUZZER OFF");
            Serial.println("✅ Accident state cleared");
            Serial.println("=========================================\n");
        }
    }
}

// ============================================================
// Send Accident Event through MQTT
// ============================================================

void sendAccidentRequest(float currentDeltaV, String reason)
{
    checkAndReconnectWiFi();

    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[MQTT] Wi-Fi not connected. Cannot send accident.");
        return;
    }

    reconnectMQTT();

    if (!mqttClient.connected())
    {
        Serial.println("[MQTT] Not connected. Cannot send accident.");
        return;
    }

    String json = "{";

    json += "\"device_id\":\"";
    json += String(DEVICE_ID);
    json += "\",";

    json += "\"latitude\":";
    json += String(TEST_LATITUDE, 6);
    json += ",";

    json += "\"longitude\":";
    json += String(TEST_LONGITUDE, 6);
    json += ",";

    json += "\"delta_v\":";
    json += String(currentDeltaV, 4);
    json += ",";

    json += "\"trigger_reason\":\"";
    json += reason;
    json += "\"";

    json += "}";

    Serial.println("\n=========================================");
    Serial.println("🚨 SENDING ACCIDENT EVENT THROUGH MQTT");
    Serial.print("Topic: ");
    Serial.println(MQTT_EVENT_TOPIC);
    Serial.print("Message: ");
    Serial.println(json);
    Serial.println("=========================================");

    bool success = mqttClient.publish(
        MQTT_EVENT_TOPIC,
        json.c_str()
    );

    if (success)
    {
        Serial.println("[MQTT] Accident event published successfully.");
    }
    else
    {
        Serial.println("[MQTT] Failed to publish accident event.");
    }
}

// ============================================================
// Send Physical Button Cancellation
// ============================================================

void sendCancelRequest()
{
    // --------------------------------------------------------
    // 1. Immediately turn OFF LED and buzzer
    // --------------------------------------------------------

    turnOffAlertOutputs();

    // --------------------------------------------------------
    // 2. Clear accident state
    // --------------------------------------------------------

    accidentTriggered = false;
    deltaV = 0.0;

    // --------------------------------------------------------
    // 3. Make sure Wi-Fi is connected
    // --------------------------------------------------------

    checkAndReconnectWiFi();

    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[MQTT] Wi-Fi not connected.");
        return;
    }

    // --------------------------------------------------------
    // 4. Make sure MQTT is connected
    // --------------------------------------------------------

    reconnectMQTT();

    if (!mqttClient.connected())
    {
        Serial.println("[MQTT] MQTT not connected.");
        return;
    }

    // --------------------------------------------------------
    // 5. Create cancellation JSON
    // --------------------------------------------------------

    String json = "{";

    json += "\"device_id\":\"";
    json += String(DEVICE_ID);
    json += "\",";

    json += "\"status\":\"cancelled\"";

    json += "}";

    // --------------------------------------------------------
    // 6. Publish cancellation
    // --------------------------------------------------------

    Serial.println("\n=========================================");
    Serial.println("🛑 PHYSICAL BUTTON PRESSED");
    Serial.println("🛑 SENDING CANCELLATION THROUGH MQTT");
    Serial.print("Topic: ");
    Serial.println(MQTT_CANCEL_TOPIC);
    Serial.print("Message: ");
    Serial.println(json);
    Serial.println("=========================================");

    bool success = mqttClient.publish(
        MQTT_CANCEL_TOPIC,
        json.c_str()
    );

    if (success)
    {
        Serial.println("[MQTT] Cancellation sent successfully.");
    }
    else
    {
        Serial.println("[MQTT] Failed to send cancellation.");
    }
}

// ============================================================
// Physical Button Check
// ============================================================

void checkCancelButton()
{
    int currentReading = digitalRead(BUTTON_CANCEL_PIN);

    if (currentReading != lastButtonReading)
    {
        lastDebounceTime = millis();
    }

    if ((millis() - lastDebounceTime) > DEBOUNCE_DELAY_MS)
    {
        if (currentReading != verifiedButtonState)
        {
            verifiedButtonState = currentReading;

            // Button pressed
            if (verifiedButtonState == LOW)
            {
                sendCancelRequest();
            }
        }
    }

    lastButtonReading = currentReading;
}

// ============================================================
// LED / Buzzer Pulse Handler
// ============================================================

void handleAlertPulsing(unsigned long currentTime)
{
    if (!accidentTriggered)
    {
        return;
    }

    if (currentTime - lastBlinkTime >= BLINK_INTERVAL_MS)
    {
        lastBlinkTime = currentTime;

        alertOutputState = !alertOutputState;

        digitalWrite(
            LED_PIN,
            alertOutputState ? HIGH : LOW
        );

        digitalWrite(
            BUZZER_PIN,
            alertOutputState ? HIGH : LOW
        );
    }
}

// ============================================================
// Setup
// ============================================================

void setup()
{
    Serial.begin(115200);

    // --------------------------------------------------------
    // Hardware pins
    // --------------------------------------------------------

    pinMode(BUTTON_CANCEL_PIN, INPUT_PULLUP);

    pinMode(LED_PIN, OUTPUT);

    pinMode(BUZZER_PIN, OUTPUT);

    // Make sure LED and buzzer start OFF
    turnOffAlertOutputs();

    // --------------------------------------------------------
    // Bluetooth initialization
    // --------------------------------------------------------

    btStart();

    // --------------------------------------------------------
    // Wi-Fi
    // --------------------------------------------------------

    WiFi.mode(WIFI_STA);

    WiFi.begin(
        WIFI_SSID,
        WIFI_PASSWORD
    );

    Serial.println("\n=========================================");
    Serial.println("ESP32 ACCIDENT DETECTION SYSTEM");
    Serial.println("=========================================");

    Serial.print("[Wi-Fi] Connecting");

    unsigned long wifiStart = millis();

    while (
        WiFi.status() != WL_CONNECTED &&
        millis() - wifiStart < 15000
    )
    {
        delay(500);

        Serial.print(".");
    }

    Serial.println();

    if (WiFi.status() == WL_CONNECTED)
    {
        Serial.println("[Wi-Fi] Connected!");

        Serial.print("[Wi-Fi] ESP32 IP: ");
        Serial.println(WiFi.localIP());
    }
    else
    {
        Serial.println("[Wi-Fi] Connection failed.");
    }

    // --------------------------------------------------------
    // MQTT
    // --------------------------------------------------------

    mqttClient.setServer(
        MQTT_BROKER,
        MQTT_PORT
    );

    mqttClient.setCallback(mqttCallback);

    reconnectMQTT();

    // --------------------------------------------------------
    // MPU6050
    // --------------------------------------------------------

    Wire.begin(21, 22);

    if (mpu.begin() != 0)
    {
        Serial.println("MPU6050 initialization failed!");

        while (1)
        {
            delay(1000);
        }
    }

    Serial.println(
        "Calibrating MPU6050... keep toy car still on flat surface."
    );

    delay(1000);

    mpu.calcOffsets(true, true);

    Serial.println("Calibration Complete!");
    Serial.println("Ready for Demo.");

    previousTime = millis();
}

// ============================================================
// Main Loop
// ============================================================

void loop()
{
    // --------------------------------------------------------
    // Maintain Wi-Fi
    // --------------------------------------------------------

    checkAndReconnectWiFi();

    // --------------------------------------------------------
    // Maintain MQTT
    // --------------------------------------------------------

    reconnectMQTT();

    mqttClient.loop();

    // --------------------------------------------------------
    // Update MPU6050
    // --------------------------------------------------------

    mpu.update();

    // --------------------------------------------------------
    // Check physical cancellation button
    // --------------------------------------------------------

    checkCancelButton();

    unsigned long currentTime = millis();

    float dt =
        (currentTime - previousTime) / 1000.0;

    previousTime = currentTime;

    // --------------------------------------------------------
    // Handle LED / buzzer
    // --------------------------------------------------------

    handleAlertPulsing(currentTime);

    // --------------------------------------------------------
    // Read accelerometer
    // --------------------------------------------------------

    float ax =
        mpu.getAccX() * 9.81;

    float ay =
        mpu.getAccY() * 9.81;

    float az =
        mpu.getAccZ() * 9.81;

    // --------------------------------------------------------
    // Calculate acceleration values
    // --------------------------------------------------------

    float horizontalImpactAccel =
        sqrt(
            ax * ax +
            ay * ay
        );

    float totalAccelMagnitude =
        sqrt(
            ax * ax +
            ay * ay +
            az * az
        );

    float linearAcceleration =
        fabs(
            totalAccelMagnitude - 9.81
        );

    // --------------------------------------------------------
    // Calculate Delta V
    // --------------------------------------------------------

    deltaV =
        (deltaV * DECAY_FACTOR) +
        (linearAcceleration * dt);

    // --------------------------------------------------------
    // Calculate tilt
    // --------------------------------------------------------

    float angleX =
        fabs(mpu.getAngleX());

    float angleY =
        fabs(mpu.getAngleY());

    float maxTiltAngle =
        max(
            angleX,
            angleY
        );

    // --------------------------------------------------------
    // Detect vertical drop
    // --------------------------------------------------------

    bool verticalDropDetected =
        (fabs(az) < LOW_Z_FREEFALL_THRESH);

    // ========================================================
    // ALERT TIMEOUT
    // ========================================================

    if (
        accidentTriggered &&
        (currentTime - lastAccidentTime > ALERT_DURATION_MS)
    )
    {
        accidentTriggered = false;

        deltaV = 0.0;

        turnOffAlertOutputs();

        Serial.println(
            "\n[SYSTEM READY] Alert window closed."
        );

        Serial.println(
            "Ready for next trigger..."
        );
    }

    // ========================================================
    // TELEMETRY
    // ========================================================

    if (!accidentTriggered)
    {
        Serial.print("Horiz Accel: ");
        Serial.print(
            horizontalImpactAccel,
            1
        );

        Serial.print(" m/s² | Tilt: ");
        Serial.print(
            maxTiltAngle,
            1
        );

        Serial.print("° | Az: ");
        Serial.print(
            az,
            1
        );

        Serial.print(" m/s² | DeltaV: ");
        Serial.println(
            deltaV,
            2
        );
    }

    // ========================================================
    // ACCIDENT DETECTION
    // ========================================================

    if (!accidentTriggered)
    {
        String triggerReason = "";

        // ----------------------------------------------------
        // Horizontal impact
        // ----------------------------------------------------

        if (
            horizontalImpactAccel >=
            XY_IMPACT_THRESH
        )
        {
            triggerReason =
                "Horizontal Impact (X/Y: " +
                String(
                    horizontalImpactAccel,
                    1
                ) +
                " m/s²)";
        }

        // ----------------------------------------------------
        // Sudden deceleration
        // ----------------------------------------------------

        else if (
            deltaV >=
            ACCIDENT_DELTA_V_THRESH
        )
        {
            triggerReason =
                "Sudden Deceleration (Delta V: " +
                String(
                    deltaV,
                    2
                ) +
                " m/s)";
        }

        // ----------------------------------------------------
        // Rollover
        // ----------------------------------------------------

        else if (
            maxTiltAngle >=
            TILT_ANGLE_THRESH
        )
        {
            triggerReason =
                "Vehicle Rollover (Tilt: " +
                String(
                    maxTiltAngle,
                    1
                ) +
                " deg)";
        }

        // ----------------------------------------------------
        // Free fall
        // ----------------------------------------------------

        else if (verticalDropDetected)
        {
            triggerReason =
                "Vertical Free-fall / Drop Detected (Az: " +
                String(
                    az,
                    1
                ) +
                " m/s²)";
        }

        // ====================================================
        // ACCIDENT TRIGGERED
        // ====================================================

        if (triggerReason != "")
        {
            Serial.println(
                "\n========================================="
            );

            Serial.print(
                "🚨 ACCIDENT DETECTED: "
            );

            Serial.println(
                triggerReason
            );

            Serial.println(
                "=========================================\n"
            );

            // Send accident to Raspberry Pi
            sendAccidentRequest(
                deltaV,
                triggerReason
            );

            // Activate accident state
            accidentTriggered = true;

            lastAccidentTime = currentTime;

            lastBlinkTime = currentTime;
        }
    }

    // --------------------------------------------------------
    // Small delay
    // --------------------------------------------------------

    delay(50);
}