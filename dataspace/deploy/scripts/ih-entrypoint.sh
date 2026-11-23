#!/bin/sh
set -e

echo "=== Starting IdentityHub entrypoint ==="

if [ -z "$EDC_VAULT_HASHICORP_URL" ]; then
    echo "ERROR: EDC_VAULT_HASHICORP_URL not defined"
    exit 1
fi

# 1. Wait for Vault API to be unsealed
echo "Waiting for Vault API at $EDC_VAULT_HASHICORP_URL..."
until RESPONSE=$(curl -s "$EDC_VAULT_HASHICORP_URL/v1/sys/health") && echo "$RESPONSE" | grep -q '"sealed":false'; do
    echo "Waiting for Vault to be unsealed... ($RESPONSE)"
    sleep 3
done
echo "Vault API is ready and unsealed."

# 2. Wait for IdentityHub Vault token
echo "Waiting for Vault token..."
until [ -s /vault-identityhub-token/identityhub-token ] || [ -s /vault-token/token ]; do
    echo "Waiting for Vault token file..."
    sleep 2
done

if [ -s /vault-identityhub-token/identityhub-token ]; then
    VAULT_TOKEN=$(cat /vault-identityhub-token/identityhub-token)
else
    VAULT_TOKEN=$(cat /vault-token/token)
fi
echo "Vault token read successfully (length: ${#VAULT_TOKEN})."

# 3. Wait for IssuerService-VC proxy to be reachable
ISSUER_HEALTH_URL="${UNIMAAS_ISSUERSERVICEVC_PROTOCOL:-http}://${UNIMAAS_ISSUERSERVICEVC_HOST:-edc-proxy-issuerservicevc}:${UNIMAAS_ISSUERSERVICEVC_PORT:-80}/api/v1/health"
echo "Waiting for IssuerService-VC at ${ISSUER_HEALTH_URL}..."
until curl -sf "$ISSUER_HEALTH_URL" >/dev/null 2>&1; do
    echo "Waiting for IssuerService-VC..."
    sleep 3
done
echo "IssuerService-VC is ready."

# 4. Wait for Keycloak and ensure OIDC realm & client exist
KC_BASE="${UNIMAAS_OIDC_IDP_PROTOCOL:-http}://${UNIMAAS_OIDC_IDP_HOST:-keycloak}:${UNIMAAS_OIDC_IDP_PORT:-8080}"
KC_REALM="${UNIMAAS_OIDC_IDP_REALM:-unimaas}"
echo "Waiting for Keycloak at ${KC_BASE}..."
until curl -sf "${KC_BASE}/realms/master" >/dev/null 2>&1; do
    echo "Waiting for Keycloak master realm..."
    sleep 3
done
echo "Keycloak is reachable. Ensuring realm '${KC_REALM}' and client '${UNIMAAS_OIDC_IDP_CLIENT_ID}' exist..."

ADMIN_TOKEN=$(curl -sf \
    --data-urlencode "username=${KEYCLOAK_ADMIN:-admin}" \
    --data-urlencode "password=${KEYCLOAK_ADMIN_PASSWORD:-admin}" \
    --data "client_id=admin-cli" \
    --data "grant_type=password" \
    "${KC_BASE}/realms/master/protocol/openid-connect/token" | jq -r '.access_token // empty' || true)

if [ -n "$ADMIN_TOKEN" ]; then
    # Ensure realm exists
    if ! curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" "${KC_BASE}/admin/realms/${KC_REALM}" >/dev/null 2>&1; then
        curl -sf -X POST "${KC_BASE}/admin/realms" \
            -H "Authorization: Bearer $ADMIN_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"realm\":\"${KC_REALM}\",\"enabled\":true,\"sslRequired\":\"none\"}" || true
    fi

    # Check if client already exists
    CLIENT_JSON=$(curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
        "${KC_BASE}/admin/realms/${KC_REALM}/clients" || echo "[]")
    EXISTING_UUID=$(echo "$CLIENT_JSON" | jq -r --arg cid "$UNIMAAS_OIDC_IDP_CLIENT_ID" '.[] | select(.clientId == $cid) | .id' | head -n 1)

    CLIENT_PAYLOAD=$(jq -nc \
        --arg cid "$UNIMAAS_OIDC_IDP_CLIENT_ID" \
        --arg secret "$UNIMAAS_OIDC_IDP_CLIENT_SECRET" \
        '{
            clientId: $cid,
            name: $cid,
            enabled: true,
            protocol: "openid-connect",
            publicClient: false,
            clientAuthenticatorType: "client-secret",
            secret: $secret,
            serviceAccountsEnabled: true,
            standardFlowEnabled: true,
            directAccessGrantsEnabled: true
        }')

    if [ -n "$EXISTING_UUID" ]; then
        curl -sf -X PUT "${KC_BASE}/admin/realms/${KC_REALM}/clients/${EXISTING_UUID}" \
            -H "Authorization: Bearer $ADMIN_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$CLIENT_PAYLOAD" || true
    else
        curl -sf -X POST "${KC_BASE}/admin/realms/${KC_REALM}/clients" \
            -H "Authorization: Bearer $ADMIN_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$CLIENT_PAYLOAD" || true
    fi
    echo "Keycloak client '${UNIMAAS_OIDC_IDP_CLIENT_ID}' configured."
fi

echo "=== Starting IdentityHub JVM ==="
exec java \
    -Dedc.vault.hashicorp.token="$VAULT_TOKEN" \
    -Djava.util.logging.config.file=/app/logging.properties \
    -Djava.security.egd=file:/dev/urandom \
    -Dedc.fs.config=./config.properties \
    -jar identity-hub.jar --log-level=debug
