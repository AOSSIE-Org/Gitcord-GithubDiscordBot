# GitCord

A Discord bot that bridges the gap between your Discord server and GitHub organization.

## Features

1.  **Authentication**: Link Discord accounts to GitHub accounts securely using GitHub Social Accounts verification.
2.  **Activity Scoring**: Automatically track PRs and Issues, awarding points to users.
3.  **Role Management**: Promote users to 'Contributor' or 'Maintainer' based on their activity score.
4.  **Task Assignment**: Automatically match open 'good-first-issue' tasks to available contributors.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    *   Rename `config.yaml` and fill in your Token, Guild ID, and Org Name.
    *   **GitHub Token**: Needs `read:org`, `read:user`, `repo` scopes.

3.  **Run**:
    ```bash
    python src/main.py
    ```

## Usage

*   **User**: `!link <github_username>` in Discord.
    *   *Prerequisite*: User must add their Discord profile link (`https://discord.com/users/<ID>`) to their GitHub Profile -> Social Accounts.
