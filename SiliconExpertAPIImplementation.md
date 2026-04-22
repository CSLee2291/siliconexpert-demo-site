# SiliconExpert API Implementation Guide

## Overview

This document describes how to properly implement the SiliconExpert Product API with support for multiple concurrent users. The key challenge is that SiliconExpert uses **session-based authentication** with cookies, which requires careful session management in multi-user environments.

## API Base URL

```
https://api.siliconexpert.com/ProductAPI/search
```

## Authentication Mechanism

### Key Discovery: Cookie-Based Sessions

The SiliconExpert API uses **cookie-based session authentication**, NOT stateless API key authentication.

| What Works | What Does NOT Work |
|------------|-------------------|
| Authenticate first, then use same session for API calls | Pass credentials only in request body/params |
| Session cookies maintained between requests | Separate sessions for auth and API calls |

### Authentication Flow

```
1. POST /authenticateUser with login + apiKey
2. Server returns success + sets session cookies
3. Subsequent API calls MUST use same session (with cookies)
4. Each API call should still include credentials in request body
```

### Authentication Endpoint

```
POST https://api.siliconexpert.com/ProductAPI/search/authenticateUser
```

**Parameters (query string):**
| Parameter | Type | Description |
|-----------|------|-------------|
| `login` | string | API username |
| `apiKey` | string | API key |

**Success Response:**
```json
{
  "Status": {
    "Code": "2",
    "Message": "Authentication Succeeded",
    "Success": "true"
  }
}
```

**Error Response (Code 39):**
```json
{
  "Status": {
    "Code": "39",
    "Message": "You are not authenticated",
    "Success": "false"
  }
}
```

## Multi-User Support: The Critical Pattern

### Problem with Global Session

```python
# WRONG - This breaks with multiple users
api_session = requests.Session()  # Global shared session

def search(part_number):
    return api_session.post(search_url, data=form_data)
```

When User A and User B access simultaneously:
1. User A authenticates (sets cookies in global session)
2. User B authenticates (overwrites cookies)
3. User A's search fails or hangs (corrupted session state)

### Solution: Per-Request Sessions

```python
# CORRECT - Each request gets its own session
def create_api_session():
    """Create a new requests session for API calls."""
    session = requests.Session()
    session.verify = False  # If needed for corporate proxies
    return session

def search(part_number):
    # Step 1: Create fresh session
    api_session = create_api_session()

    # Step 2: Authenticate in this session
    auth_response = api_session.post(auth_url, params=auth_params)

    # Step 3: Make API call in SAME session (cookies preserved)
    return api_session.post(search_url, data=form_data)
```

## API Endpoints

### 1. User Status (Account Info & Quota)

```
POST /userStatus
```

**Requires:** Prior authentication in same session

**Response:**
```json
{
  "Status": { "Code": "0", "Message": "Successful Operation", "Success": "true" },
  "UserStatus": {
    "CreationDate": "January 22, 2024",
    "ExpirationDate": "July 29, 2026",
    "PartDetailLimit": "10000",
    "PartDetailCount": "9370",
    "PartDetailRemaining": "630",
    "AclPartsCount": "177"
  }
}
```

### 2. Keyword Search (Part Search)

```
POST /partsearch
```

**Form Data:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `partNumber` | string | Part number to search |
| `login` | string | API username |
| `apiKey` | string | API key |
| `fmt` | string | Response format (`json`) |

**Response:**
```json
{
  "Status": { "Code": "0", "Message": "Successful Operation", "Success": "true" },
  "TotalItems": "Total Items: 2, Shown Items: 2",
  "Result": [
    {
      "ComID": "425058279",
      "PartNumber": "MAX15095AGFC+",
      "Manufacturer": "Analog Devices",
      "PlName": "Hot Swap Controllers",
      "Description": "Hot Swap Controller 1-CH 18V...",
      "Lifecycle": "Active",
      "RoHS": "Yes",
      "YEOL": "17.9"
    }
  ]
}
```

### 3. List Part Search (Multiple Parts)

```
POST /listPartSearch
```

**Form Data:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `partNumber` | JSON string | Array of part objects |
| `login` | string | API username |
| `apiKey` | string | API key |
| `fmt` | string | Response format (`json`) |

**Part Number Format:**
```json
[
  {"partNumber": "MAX15095AGFC+"},
  {"partNumber": "LM358", "manufacturer": "Texas Instruments"}
]
```

### 4. Part Detail (with Lifecycle Data)

```
POST /partDetail
```

**Form Data:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `comIds` | string | Comma-separated ComID list |
| `login` | string | API username |
| `apiKey` | string | API key |
| `fmt` | string | Response format (`json`) |
| `getLifeCycleData` | string | Set to `1` for lifecycle info |

**Batch Limit:** Maximum 50 ComIDs per request

**Response:**
```json
{
  "Status": { "Code": "0", "Message": "Successful Operation", "Success": "true" },
  "Results": {
    "ResultDto": [
      {
        "RequestedComID": "425058279",
        "LifeCycleData": {
          "PartStatus": "Active",
          "EstimatedYearsToEOL": "8.6",
          "EstimatedEOLDate": "2035",
          "PartLifecycleStage": "Mature",
          "LifeCycleRiskGrade": "Medium",
          "OverallRisk": "58.8%"
        }
      }
    ]
  }
}
```

## Complete Implementation Pattern (Python/Flask)

```python
import requests

API_BASE_URL = "https://api.siliconexpert.com/ProductAPI/search"
API_CREDENTIALS = {
    "login": "your_login",
    "apiKey": "your_api_key"
}

def create_api_session():
    """Create a new requests session for API calls.

    Each request should use its own session to avoid concurrency issues
    when multiple users access the app simultaneously.
    """
    session = requests.Session()
    session.verify = False  # Disable for corporate proxies if needed
    return session


def authenticate_session(api_session):
    """Authenticate within the given session.

    Returns: (success: bool, result: dict)
    """
    auth_url = f"{API_BASE_URL}/authenticateUser"
    auth_params = {
        "login": API_CREDENTIALS["login"],
        "apiKey": API_CREDENTIALS["apiKey"]
    }

    response = api_session.post(auth_url, params=auth_params)
    response.raise_for_status()
    result = response.json()

    success = result.get("Status", {}).get("Success", "false").lower() == "true"
    return success, result


def keyword_search(part_number):
    """Search for parts by keyword with multi-user support."""
    # Step 1: Create fresh session for this request
    api_session = create_api_session()

    # Step 2: Authenticate first (required)
    auth_success, auth_result = authenticate_session(api_session)
    if not auth_success:
        return {"error": "Authentication failed", "details": auth_result}

    # Step 3: Perform search in same session (cookies maintained)
    search_url = f"{API_BASE_URL}/partsearch"
    form_data = {
        "partNumber": part_number,
        "login": API_CREDENTIALS["login"],
        "apiKey": API_CREDENTIALS["apiKey"],
        "fmt": "json"
    }

    response = api_session.post(
        search_url,
        data=form_data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    response.raise_for_status()
    return response.json()


def get_part_details(com_ids):
    """Fetch part details with lifecycle data.

    Args:
        com_ids: List of ComID strings (max 50 per batch)
    """
    # Step 1: Create fresh session
    api_session = create_api_session()

    # Step 2: Authenticate first
    auth_success, auth_result = authenticate_session(api_session)
    if not auth_success:
        return {"error": "Authentication failed", "details": auth_result}

    # Step 3: Fetch part details in same session
    detail_url = f"{API_BASE_URL}/partDetail"
    form_data = {
        "comIds": ",".join(com_ids),
        "login": API_CREDENTIALS["login"],
        "apiKey": API_CREDENTIALS["apiKey"],
        "fmt": "json",
        "getLifeCycleData": "1"
    }

    response = api_session.post(
        detail_url,
        data=form_data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    response.raise_for_status()
    return response.json()
```

## Status Codes

| Code | Message | Description |
|------|---------|-------------|
| `0` | Successful Operation | Request completed successfully |
| `2` | Authentication Succeeded | Login successful |
| `39` | You are not authenticated | Missing auth or expired session |

## Best Practices

### 1. Always Authenticate Per-Request
```python
def any_api_call():
    session = create_api_session()
    authenticate_session(session)  # Always do this first
    # ... then make API call
```

### 2. Handle Batch Limits
```python
def get_all_part_details(com_ids):
    """Handle more than 50 ComIDs by batching."""
    results = []
    batch_size = 50

    for i in range(0, len(com_ids), batch_size):
        batch = com_ids[i:i + batch_size]
        result = get_part_details(batch)
        results.extend(result.get("Results", {}).get("ResultDto", []))

    return results
```

### 3. Check Response Status
```python
def check_api_response(result):
    """Check if API response indicates success."""
    status = result.get("Status", {})
    success = status.get("Success", "false").lower() == "true"
    return success, status.get("Message", "Unknown error")
```

### 4. Handle SSL for Corporate Proxies
```python
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
session.verify = False  # Only if behind corporate proxy with self-signed certs
```

## Environment Variables

Recommended configuration:

```env
SILICONEXPERT_LOGIN=your_api_login
SILICONEXPERT_API_KEY=your_api_key
```

```python
import os
from dotenv import load_dotenv

load_dotenv()

API_CREDENTIALS = {
    "login": os.getenv('SILICONEXPERT_LOGIN'),
    "apiKey": os.getenv('SILICONEXPERT_API_KEY')
}
```

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Session** | Create new `requests.Session()` per request |
| **Authentication** | Always authenticate first in each session |
| **Cookies** | Maintained automatically by `requests.Session()` |
| **Credentials** | Include in both auth params AND form data |
| **Concurrency** | Safe with per-request sessions |
| **Batch Limit** | 50 ComIDs max for partDetail API |

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| "You are not authenticated" (Code 39) | Missing auth or different session | Authenticate before API call in same session |
| Request hangs with multiple users | Global shared session | Use per-request sessions |
| SSL errors | Corporate proxy | Set `session.verify = False` |
| Empty results | Wrong ComID format | Ensure ComIDs are strings, not floats |
