"""Authentication helpers for Habitat PTAC integration."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import boto3
from pycognito import Cognito

from .const import (
    AWS_REGION,
    COGNITO_CLIENT_ID,
    COGNITO_IDENTITY_POOL_ID,
    COGNITO_REGION,
    COGNITO_USER_POOL_ID,
    DYNAMODB_TABLE,
    THERMOSTAT_MODEL_SUFFIX,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class AWSCredentials:
    """Temporary AWS credentials from Cognito Identity Pool."""

    access_key_id: str
    secret_key: str
    session_token: str
    expiration: Any


@dataclass
class DeviceInfo:
    """Information about a discovered PTAC device."""

    gateway_id: str
    thing_name: str
    name: str


def authenticate(username: str, password: str) -> tuple[str, str, str]:
    """Authenticate with Cognito SRP.

    Returns (id_token, access_token, refresh_token).
    """
    user = Cognito(
        COGNITO_USER_POOL_ID,
        COGNITO_CLIENT_ID,
        username=username,
        user_pool_region=COGNITO_REGION,
    )
    user.authenticate(password=password)
    return user.id_token, user.access_token, user.refresh_token


def refresh_tokens(username: str, refresh_token: str) -> tuple[str, str]:
    """Use a refresh token to get new Cognito tokens.

    Returns (id_token, access_token).
    """
    user = Cognito(
        COGNITO_USER_POOL_ID,
        COGNITO_CLIENT_ID,
        username=username,
        user_pool_region=COGNITO_REGION,
        refresh_token=refresh_token,
    )
    user.renew_access_token()
    return user.id_token, user.access_token


def get_identity_id(id_token: str) -> str:
    """Get the Cognito Identity ID for this user."""
    client = boto3.client("cognito-identity", region_name=COGNITO_REGION)
    response = client.get_id(
        IdentityPoolId=COGNITO_IDENTITY_POOL_ID,
        Logins={
            f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}": id_token
        },
    )
    return response["IdentityId"]


def get_aws_credentials(identity_id: str, id_token: str) -> AWSCredentials:
    """Exchange Cognito ID token for temporary AWS IoT credentials."""
    client = boto3.client("cognito-identity", region_name=COGNITO_REGION)
    response = client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={
            f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}": id_token
        },
    )
    creds = response["Credentials"]
    return AWSCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_key=creds["SecretKey"],
        session_token=creds["SessionToken"],
        expiration=creds["Expiration"],
    )


def get_devices(identity_id: str, credentials: AWSCredentials) -> list[DeviceInfo]:
    """Fetch the list of paired devices from DynamoDB."""
    client = boto3.client(
        "dynamodb",
        region_name=AWS_REGION,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_key,
        aws_session_token=credentials.session_token,
    )
    response = client.query(
        TableName=DYNAMODB_TABLE,
        KeyConditionExpression="userid = :uid",
        ExpressionAttributeValues={":uid": {"S": identity_id}},
    )

    devices: list[DeviceInfo] = []
    for item in response.get("Items", []):
        own_str = item.get("Own", {}).get("S", "{}")
        try:
            own_data = json.loads(own_str)
        except json.JSONDecodeError:
            _LOGGER.warning("Could not parse device list: %s", own_str)
            continue

        for gateway_id in own_data.get("list", []):
            thing_name = f"{gateway_id}{THERMOSTAT_MODEL_SUFFIX}"
            # Use last 12 chars of gateway ID (MAC address portion) as display name
            short_id = gateway_id.split("-")[-1] if "-" in gateway_id else gateway_id[-12:]
            devices.append(
                DeviceInfo(
                    gateway_id=gateway_id,
                    thing_name=thing_name,
                    name=f"Habitat PTAC {short_id}",
                )
            )

    return devices
