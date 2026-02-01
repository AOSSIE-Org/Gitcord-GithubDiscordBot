import requests
import logging

class GitHubClient:
    def __init__(self, token, org_name):
        self.token = token
        self.org_name = org_name
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.graphql_url = "https://api.github.com/graphql"
        self.rest_url = "https://api.github.com"

    def verify_identity(self, github_username, discord_id):
        """
        Verifies if the GitHub user has linked the specific Discord ID in their social accounts.
        using GraphQL.
        """
        query = """
        query($username: String!) {
          user(login: $username) {
            socialAccounts(first: 10) {
              nodes {
                provider
                url
              }
            }
          }
        }
        """
        variables = {"username": github_username}
        
        try:
            response = requests.post(
                self.graphql_url,
                json={"query": query, "variables": variables},
                headers=self.headers
            )
            response.raise_for_status()
            data = response.json()
            
            if "errors" in data:
                logging.error(f"GraphQL Error: {data['errors']}")
                return False

            user_data = data.get("data", {}).get("user")
            if not user_data:
                return False

            socials = user_data.get("socialAccounts", {}).get("nodes", [])
            target_url = f"https://discord.com/users/{discord_id}"
            
            for account in socials:
                # Some users might just put the ID, but the requirement is the full URL or check provider
                # The prompt asks: "passes ONLY if one of the links is exactly https://discord.com/users/<DISCORD_USER_ID>"
                if account["url"] == target_url:
                    return True
            
            return False

        except Exception as e:
            logging.error(f"Verification failed: {e}")
            return False

    def get_user_activity(self, github_username, since_date=None):
        """
        Fetches recent activity for a user in the organization.
        For simplicity, we query issues and PRs created by the user in the org repositories.
        Using REST API search for broader reach or Events API.
        
        Using Search API to find Issues and PRs within the Org.
        """
        # Search for Issues and PRs created by author in org
        query = f"org:{self.org_name} author:{github_username} created:>{since_date if since_date else '2020-01-01'}"
        url = f"{self.rest_url}/search/issues?q={query}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as e:
            logging.error(f"Failed to fetch activity for {github_username}: {e}")
            return []

    def get_repo_events(self, owner, name, etag=None):
        """
        Fetches events for a repository.
        Uses ETag to check for updates efficiently.
        """
        url = f"{self.rest_url}/repos/{owner}/{name}/events"
        headers = self.headers.copy()
        if etag:
            headers['If-None-Match'] = etag
            
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 304:
                return [], etag # No new events
            
            response.raise_for_status()
            new_etag = response.headers.get('ETag')
            return response.json(), new_etag
        except Exception as e:
            logging.error(f"Failed to fetch events for {owner}/{name}: {e}")
            return [], etag

    def get_open_issues_with_label(self, label):
        """
        Finds open issues in the org with a specific label.
        """
        query = f"org:{self.org_name} is:issue is:open label:\"{label}\""
        url = f"{self.rest_url}/search/issues?q={query}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as e:
            logging.error(f"Failed to fetch issues with label {label}: {e}")
            return []
