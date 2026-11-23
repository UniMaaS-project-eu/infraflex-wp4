#!/usr/bin/env python3
"""
GraphDB REST & SPARQL Client for UniMaaS / Infraflex.

Provides connection management, automatic repository provisioning,
SPARQL 1.1 Query & Update execution, and direct RDF statement manipulation
for Ontotext GraphDB.
"""

import json
import logging
from typing import Any, Optional
import requests

log = logging.getLogger("graphdb_client")


class GraphDBClient:
    """Client for Ontotext GraphDB standard REST & SPARQL 1.1 APIs."""

    def __init__(
        self,
        base_url: str = "http://localhost:7200",
        repository: str = "infraflex",
        user: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 15,
    ):
        self.base_url = base_url.rstrip("/")
        self.repository = repository
        self.timeout = timeout
        self.session = requests.Session()
        if user and password:
            self.session.auth = (user, password)

    @property
    def query_url(self) -> str:
        return f"{self.base_url}/repositories/{self.repository}"

    @property
    def statements_url(self) -> str:
        return f"{self.base_url}/repositories/{self.repository}/statements"

    def is_healthy(self) -> bool:
        """Check if GraphDB service is reachable and responding."""
        try:
            r = self.session.get(f"{self.base_url}/rest/repositories", timeout=self.timeout)
            return r.status_code == 200
        except Exception as exc:
            log.debug("GraphDB health check failed: %s", exc)
            return False

    def repository_exists(self) -> bool:
        """Check whether the target repository exists in GraphDB."""
        try:
            r = self.session.get(f"{self.base_url}/rest/repositories/{self.repository}", timeout=self.timeout)
            if r.status_code == 200:
                return True
            if r.status_code == 404:
                return False
            # Fallback to checking the repository list
            r_all = self.session.get(f"{self.base_url}/rest/repositories", timeout=self.timeout)
            if r_all.status_code == 200:
                repos = r_all.json()
                return any(repo.get("id") == self.repository for repo in repos)
        except Exception as exc:
            log.warning("Failed checking repository existence: %s", exc)
        return False

    def ensure_repository(self, ruleset: str = "rdfsplus-optimized") -> bool:
        """Ensure the repository exists in GraphDB, creating it if necessary."""
        if self.repository_exists():
            log.info("GraphDB repository '%s' already exists.", self.repository)
            return True

        log.info("Creating GraphDB repository '%s' with ruleset '%s'...", self.repository, ruleset)

        # GraphDB 10 JSON config format
        repo_json = {
            "id": self.repository,
            "title": f"{self.repository.capitalize()} Knowledge Graph",
            "type": "free",
            "params": {
                "ruleset": {"value": ruleset},
                "baseURL": {"value": "https://w3id.org/fm/infraflex#"},
                "defaultNS": {"value": "https://w3id.org/fm/infraflex#"},
                "entityIndexSize": {"value": "10000000"},
            },
        }

        try:
            r = self.session.post(
                f"{self.base_url}/rest/repositories",
                headers={"Content-Type": "application/json"},
                json=repo_json,
                timeout=self.timeout,
            )
            if r.status_code in (200, 201):
                log.info("Successfully created GraphDB repository '%s' (JSON API).", self.repository)
                return True
        except Exception as exc:
            log.debug("JSON repository creation failed (%s), attempting Turtle config...", exc)

        # Fallback: multipart Turtle configuration (compatible with GraphDB 8, 9, 10)
        repo_ttl = f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rep: <http://www.openrdf.org/config/repository#> .
@prefix sr: <http://www.openrdf.org/config/repository/sail#> .
@prefix sail: <http://www.openrdf.org/config/sail#> .
@prefix owlim: <http://www.ontotext.com/trree/owlim#> .

[] a rep:Repository ;
    rep:repositoryID "{self.repository}" ;
    rdfs:label "{self.repository.capitalize()} Knowledge Graph" ;
    rep:repositoryImpl [
        rep:repositoryType "openrdf:SailRepository" ;
        sr:sailImpl [
            sail:sailType "owlim:Sail" ;
            owlim:base-URL "https://w3id.org/fm/infraflex#" ;
            owlim:defaultNS "https://w3id.org/fm/infraflex#" ;
            owlim:entity-index-size "10000000" ;
            owlim:ruleset "{ruleset}"
        ]
    ] .
"""
        try:
            files = {"config": ("config.ttl", repo_ttl, "text/turtle")}
            r = self.session.post(
                f"{self.base_url}/rest/repositories",
                files=files,
                timeout=self.timeout,
            )
            if r.status_code in (200, 201):
                log.info("Successfully created GraphDB repository '%s' (Turtle config).", self.repository)
                return True
            log.error("Failed to create GraphDB repository: HTTP %d: %s", r.status_code, r.text)
        except Exception as exc:
            log.error("Exception during GraphDB repository creation: %s", exc)

        return False

    def update(self, sparql_update: str) -> bool:
        """Execute a SPARQL 1.1 Update statement."""
        try:
            r = self.session.post(
                self.statements_url,
                headers={"Content-Type": "application/sparql-update; charset=utf-8"},
                data=sparql_update.encode("utf-8"),
                timeout=self.timeout,
            )
            if r.status_code in (200, 204):
                return True
            # Also try URL-encoded form data fallback
            r = self.session.post(
                self.statements_url,
                data={"update": sparql_update},
                timeout=self.timeout,
            )
            if r.status_code in (200, 204):
                return True
            log.error("SPARQL Update failed: HTTP %d: %s", r.status_code, r.text)
            return False
        except Exception as exc:
            log.error("SPARQL Update exception: %s", exc)
            return False

    def query(self, sparql_query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL 1.1 Query and return a list of result dictionaries."""
        try:
            r = self.session.post(
                self.query_url,
                headers={"Accept": "application/sparql-results+json"},
                data={"query": sparql_query},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            results = []
            bindings = data.get("results", {}).get("bindings", [])
            for b in bindings:
                row = {}
                for var, val in b.items():
                    row[var] = val.get("value")
                results.append(row)
            return results
        except Exception as exc:
            log.error("SPARQL Query exception: %s", exc)
            return []

    def insert_turtle(self, turtle_data: str, context: Optional[str] = None) -> bool:
        """Insert raw RDF statements in Turtle format."""
        params = {}
        if context:
            params["context"] = f"<{context}>"
        try:
            r = self.session.post(
                self.statements_url,
                headers={"Content-Type": "text/turtle; charset=utf-8"},
                data=turtle_data.encode("utf-8"),
                params=params,
                timeout=self.timeout,
            )
            return r.status_code in (200, 204)
        except Exception as exc:
            log.error("Turtle insert exception: %s", exc)
            return False

    def close(self):
        self.session.close()

