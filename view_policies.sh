#!/usr/bin/env bash
# View policy and user-role mappings for SLM-Forge
# Usage: ./view_policies.sh [command]
# Commands: users, roles, matrix, test

set -euo pipefail

KEYCLOAK_URL="http://localhost:8080"
OPA_URL="http://localhost:8181"
REALM="slm-forge"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

command="${1:-help}"

case "$command" in
  users)
    echo -e "${BLUE}=== User → Role Mappings ===${NC}\n"
    echo "Fetching from Keycloak realm export..."
    cat keycloak/realm-export.json | jq -r '
      .users[] | 
      "\(.username) → \(.realmRoles | join(", "))"
    '
    ;;

  roles)
    echo -e "${BLUE}=== Available Roles ===${NC}\n"
    cat keycloak/realm-export.json | jq -r '
      .roles.realm[] | 
      "• \(.name)\n  \(.description)\n"
    '
    ;;

  matrix)
    echo -e "${BLUE}=== Role → Permissions Matrix ===${NC}\n"
    echo "Source: policies/role_matrix.rego"
    echo ""
    cat policies/role_matrix.rego | grep -A 8 'matrix\["' | sed 's/^/  /'
    ;;

  test)
    echo -e "${BLUE}=== Testing OPA Policy Decisions ===${NC}\n"
    
    # Test 1: Admin can delete runs
    echo -e "${GREEN}Test 1: admin can delete run?${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/allow \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "admin@local", "roles": ["admin"]},
          "action": "delete",
          "resource": "run"
        }
      }' | jq .
    
    echo ""
    
    # Test 2: Data engineer cannot delete exports
    echo -e "${GREEN}Test 2: data_engineer can delete export?${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/allow \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "engineer@local", "roles": ["data_engineer"]},
          "action": "delete",
          "resource": "export"
        }
      }' | jq .
    
    echo -e "\n${YELLOW}Getting denial reason:${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/reason \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "engineer@local", "roles": ["data_engineer"]},
          "action": "delete",
          "resource": "export"
        }
      }' | jq .
    
    echo ""
    
    # Test 3: Domain expert can create research
    echo -e "${GREEN}Test 3: domain_expert can create research?${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/allow \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "expert@local", "roles": ["domain_expert"]},
          "action": "create",
          "resource": "research"
        }
      }' | jq .
    
    echo ""
    
    # Test 4: Support cannot update anything
    echo -e "${GREEN}Test 4: support can update dataset?${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/allow \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "support@local", "roles": ["support"]},
          "action": "update",
          "resource": "dataset"
        }
      }' | jq .
    
    echo -e "\n${YELLOW}Getting denial reason:${NC}"
    curl -fsS -X POST ${OPA_URL}/v1/data/slm_forge/reason \
      -H 'Content-Type: application/json' \
      -d '{
        "input": {
          "user": {"id": "support@local", "roles": ["support"]},
          "action": "update",
          "resource": "dataset"
        }
      }' | jq .
    ;;

  keycloak)
    echo -e "${BLUE}=== Keycloak Admin Console ===${NC}\n"
    echo "URL: ${KEYCLOAK_URL}"
    echo "Username: admin"
    echo "Password: admin"
    echo ""
    echo "Navigate to: Realm Settings > slm-forge > Users"
    echo "Or: Realm Settings > slm-forge > Roles"
    ;;

  all)
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║         SLM-Forge Policy & Role Mappings                   ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"
    
    $0 users
    echo ""
    $0 roles
    echo ""
    $0 matrix
    ;;

  help|*)
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  users      - Show user → role mappings"
    echo "  roles      - Show all available roles with descriptions"
    echo "  matrix     - Show role → permissions matrix"
    echo "  test       - Run sample OPA policy tests"
    echo "  keycloak   - Show Keycloak admin console info"
    echo "  all        - Show users, roles, and matrix"
    echo "  help       - Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 users"
    echo "  $0 test"
    echo "  $0 all"
    ;;
esac
