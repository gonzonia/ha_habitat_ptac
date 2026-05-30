"""Constants for the Habitat PTAC integration."""

DOMAIN = "habitat_ptac"

# AWS Cognito
COGNITO_REGION = "us-west-2"
COGNITO_USER_POOL_ID = "us-west-2_UqKk6Qvs1"
# Public app-wide client ID from Habitat app v1.8.0 — same for all users, not a secret
COGNITO_CLIENT_ID = "ji4tv7q81n7rbbmv1bkmkeb8i"
COGNITO_IDENTITY_POOL_ID = "us-west-2:ba429fe0-7865-4c71-8715-287b89ec7b5f"

# AWS IoT Core
IOT_ENDPOINT = "asyh9zqgbddbc-ats.iot.us-west-2.amazonaws.com"
AWS_REGION = "us-west-2"

# DynamoDB
DYNAMODB_TABLE = "UserToDeviceList"

# MQTT Shadow topics
SHADOW_UPDATE_TOPIC = "$aws/things/{}/shadow/update"
SHADOW_ACCEPTED_TOPIC = "$aws/things/{}/shadow/update/accepted"
SHADOW_GET_TOPIC = "$aws/things/{}/shadow/get"
SHADOW_GET_ACCEPTED_TOPIC = "$aws/things/{}/shadow/get/accepted"



# Device addressing
THERMOSTAT_ENDPOINT = "000000000003"
THERMOSTAT_MODEL_SUFFIX = "-SAUPTZ1PT868-0000000000000000"
SERVICE_NS = "ep0:sPTAC868"

# SystemMode values
SYSTEM_MODE_OFF = 0
SYSTEM_MODE_HEAT = 4 
SYSTEM_MODE_COOL = 3 
SYSTEM_MODE_FAN_ONLY = 7

HA_TO_DEVICE_MODE = {
    "off": SYSTEM_MODE_OFF,
    "heat": SYSTEM_MODE_HEAT,
    "cool": SYSTEM_MODE_COOL,
    "fan_only": SYSTEM_MODE_FAN_ONLY,
}
DEVICE_TO_HA_MODE = {v: k for k, v in HA_TO_DEVICE_MODE.items()}

# FanMode values
FAN_MODE_AUTO = 5
FAN_MODE_LOW = 1
FAN_MODE_HIGH = 3

HA_TO_DEVICE_FAN = {
    "auto": FAN_MODE_AUTO,
    "low": FAN_MODE_LOW,
    "high": FAN_MODE_HIGH,
}
DEVICE_TO_HA_FAN = {v: k for k, v in HA_TO_DEVICE_FAN.items()}

# Temperature
TEMP_MULTIPLIER = 100

# Running state
RUNNING_STATE_ACTIVE = 32

# Config entry keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_IDENTITY_ID = "identity_id"
CONF_DEVICES = "devices"
CONF_REFRESH_TOKEN = "refresh_token"

# Credential refresh interval (credentials expire in 3600s, refresh at 50min)
CREDENTIAL_REFRESH_INTERVAL_MINUTES = 50