"""Auth0 integration for OAuth2 authentication (optional)."""

import os
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, status

# Auth0 Configuration
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "")
AUTH0_CALLBACK_URL = os.getenv("AUTH0_CALLBACK_URL", "http://localhost:8000/auth/auth0/callback")


def is_auth0_configured() -> bool:
    """
    Check if Auth0 is properly configured.

    Returns:
        True if all Auth0 environment variables are set
    """
    return bool(AUTH0_DOMAIN and AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET and AUTH0_AUDIENCE)


def get_auth0_login_url(state: Optional[str] = None) -> str:
    """
    Generate Auth0 login URL for OAuth2 flow.

    Args:
        state: Optional state parameter for CSRF protection

    Returns:
        Auth0 authorization URL

    Example:
        >>> url = get_auth0_login_url(state="random_state_token")
        >>> print(url[:30])
        https://your-tenant.auth0.com/
    """
    if not is_auth0_configured():
        raise ValueError("Auth0 is not configured")

    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": AUTH0_CALLBACK_URL,
        "scope": "openid profile email",
        "audience": AUTH0_AUDIENCE,
    }

    if state:
        params["state"] = state

    return f"https://{AUTH0_DOMAIN}/authorize?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access and ID tokens.

    Args:
        code: Authorization code from Auth0 callback

    Returns:
        Dictionary with access_token, id_token, token_type, expires_in

    Raises:
        HTTPException: If token exchange fails

    Example:
        >>> tokens = await exchange_code_for_tokens("auth_code_here")
        >>> print(tokens["access_token"][:20])
        eyJhbGciOiJSUzI1NiIs...
    """
    if not is_auth0_configured():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth0 is not configured"
        )

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"

    payload = {
        "grant_type": "authorization_code",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "code": code,
        "redirect_uri": AUTH0_CALLBACK_URL,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(token_url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to exchange code for tokens: {str(e)}"
            )


async def get_auth0_user_info(access_token: str) -> Dict[str, Any]:
    """
    Get user information from Auth0 using access token.

    Args:
        access_token: Auth0 access token

    Returns:
        User information dictionary

    Raises:
        HTTPException: If user info retrieval fails

    Example:
        >>> user_info = await get_auth0_user_info(access_token)
        >>> print(user_info["email"])
        user@example.com
    """
    if not is_auth0_configured():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth0 is not configured"
        )

    userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to get user info: {str(e)}"
            )


def verify_auth0_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Auth0 JWT token.

    This fetches the JWKS from Auth0 and verifies the token signature.

    Args:
        token: Auth0 JWT token

    Returns:
        Decoded token payload if valid, None otherwise

    Example:
        >>> payload = verify_auth0_token(id_token)
        >>> print(payload["sub"])
        auth0|123456789
    """
    if not is_auth0_configured():
        return None

    jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"

    try:
        # Get JWKS from Auth0
        import requests
        jwks = requests.get(jwks_url).json()

        # Get the key ID from token header
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}

        # Find the matching key
        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }
                break

        if not rsa_key:
            return None

        # Verify and decode token
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/"
        )

        return payload

    except JWTError as e:
        print(f"Auth0 token verification failed: {e}")
        return None
    except Exception as e:
        print(f"Error verifying Auth0 token: {e}")
        return None


async def logout_from_auth0(return_to: str) -> str:
    """
    Generate Auth0 logout URL.

    Args:
        return_to: URL to return to after logout

    Returns:
        Auth0 logout URL

    Example:
        >>> url = await logout_from_auth0("http://localhost:3000")
        >>> print(url[:30])
        https://your-tenant.auth0.com/
    """
    if not is_auth0_configured():
        raise ValueError("Auth0 is not configured")

    params = {
        "client_id": AUTH0_CLIENT_ID,
        "returnTo": return_to,
    }

    return f"https://{AUTH0_DOMAIN}/v2/logout?{urlencode(params)}"


def extract_auth0_user_id(auth0_sub: str) -> str:
    """
    Extract user ID from Auth0 subject claim.

    Auth0 subject format: "auth0|123456789" or "google-oauth2|123456789"

    Args:
        auth0_sub: Auth0 subject claim

    Returns:
        User ID portion (after the pipe)

    Example:
        >>> extract_auth0_user_id("auth0|123456789")
        '123456789'
        >>> extract_auth0_user_id("google-oauth2|987654321")
        '987654321'
    """
    if "|" in auth0_sub:
        return auth0_sub.split("|")[1]
    return auth0_sub


def get_auth0_provider(auth0_sub: str) -> str:
    """
    Extract provider from Auth0 subject claim.

    Args:
        auth0_sub: Auth0 subject claim

    Returns:
        Provider name (e.g., "auth0", "google-oauth2", "github")

    Example:
        >>> get_auth0_provider("auth0|123456789")
        'auth0'
        >>> get_auth0_provider("google-oauth2|987654321")
        'google-oauth2'
    """
    if "|" in auth0_sub:
        return auth0_sub.split("|")[0]
    return "unknown"
