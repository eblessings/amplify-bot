#!/bin/bash

# Complete GitHub Upload Script for Amplify Bot
# Run this script from your project root directory on your GPU

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/eblessings/amplify-bot.git"
REPO_NAME="amplify-bot"
BRANCH_NAME="main"

echo -e "${BLUE}=== Amplify Bot GitHub Upload Script ===${NC}"
echo -e "${YELLOW}This script will upload your current work to GitHub${NC}"
echo

# Step 1: Verify Git is installed
echo -e "${BLUE}Step 1: Checking Git installation...${NC}"
if ! command -v git &> /dev/null; then
    echo -e "${RED}Error: Git is not installed. Please install Git first.${NC}"
    echo "Ubuntu/Debian: sudo apt-get install git"
    echo "CentOS/RHEL: sudo yum install git"
    exit 1
fi
echo -e "${GREEN}✓ Git is installed${NC}"

# Step 2: Verify we're in the correct directory
echo -e "${BLUE}Step 2: Verifying project directory...${NC}"
if [ ! -f "package.json" ] && [ ! -f "requirements.txt" ] && [ ! -f "README.md" ]; then
    echo -e "${YELLOW}Warning: No common project files found. Are you in the correct directory?${NC}"
    echo "Current directory: $(pwd)"
    read -p "Continue anyway? (y/N): " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo -e "${GREEN}✓ Project directory verified${NC}"

# Step 3: Initialize Git if not already initialized
echo -e "${BLUE}Step 3: Initializing Git repository...${NC}"
if [ ! -d ".git" ]; then
    git init
    echo -e "${GREEN}✓ Git repository initialized${NC}"
else
    echo -e "${GREEN}✓ Git repository already exists${NC}"
fi

# Step 4: Configure Git user (if not already configured)
echo -e "${BLUE}Step 4: Configuring Git user...${NC}"
if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
    echo "Git user not configured. Please provide your details:"
    read -p "Enter your name: " git_name
    read -p "Enter your email: " git_email
    git config user.name "$git_name"
    git config user.email "$git_email"
    echo -e "${GREEN}✓ Git user configured${NC}"
else
    echo -e "${GREEN}✓ Git user already configured${NC}"
    echo "Name: $(git config user.name)"
    echo "Email: $(git config user.email)"
fi

# Step 5: Create/Update .gitignore
echo -e "${BLUE}Step 5: Setting up .gitignore...${NC}"
cat > .gitignore << 'EOF'
# Dependencies
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
ENV/
env.bak/
venv.bak/
.env
.venv

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# OS generated files
.DS_Store
.DS_Store?
._*
.Spotlight-V100
.Trashes
ehthumbs.db
Thumbs.db

# Logs
logs
*.log

# Runtime data
pids
*.pid
*.seed
*.pid.lock

# Coverage directory used by tools like istanbul
coverage/

# nyc test coverage
.nyc_output

# Grunt intermediate storage
.grunt

# Bower dependency directory
bower_components

# node-waf configuration
.lock-wscript

# Compiled binary addons
build/Release

# Dependency directories
jspm_packages/

# Optional npm cache directory
.npm

# Optional REPL history
.node_repl_history

# Output of 'npm pack'
*.tgz

# Yarn Integrity file
.yarn-integrity

# dotenv environment variables file
.env
.env.local
.env.development.local
.env.test.local
.env.production.local

# AWS Amplify
amplify/\#current-cloud-backend
amplify/.config/local-*
amplify/logs
amplify/mock-data
amplify/backend/amplify-meta.json
amplify/backend/awscloudformation
amplify/backend/.temp
build/
dist/
node_modules/
aws-exports.js
awsconfiguration.json
amplifyconfiguration.json
amplifyconfiguration.dart
amplify-build-config.json
amplify-gradle-config.json
amplifytools.xcconfig
.secret-*

# GPU specific files
*.gpu
*.cuda
*.cubins
*.fatbin
*.ptx

# Large model files (add specific extensions as needed)
*.bin
*.safetensors
*.ckpt
*.pth
*.h5

# Temporary files
*.tmp
*.temp
temp/
tmp/
EOF
echo -e "${GREEN}✓ .gitignore created/updated${NC}"

# Step 6: Check if remote origin exists
echo -e "${BLUE}Step 6: Configuring remote repository...${NC}"
if git remote get-url origin &> /dev/null; then
    current_remote=$(git remote get-url origin)
    if [ "$current_remote" != "$REPO_URL" ]; then
        echo -e "${YELLOW}Warning: Remote origin exists but points to different URL${NC}"
        echo "Current: $current_remote"
        echo "Expected: $REPO_URL"
        read -p "Update remote URL? (y/N): " update_remote
        if [[ $update_remote =~ ^[Yy]$ ]]; then
            git remote set-url origin "$REPO_URL"
            echo -e "${GREEN}✓ Remote URL updated${NC}"
        fi
    else
        echo -e "${GREEN}✓ Remote origin already configured${NC}"
    fi
else
    git remote add origin "$REPO_URL"
    echo -e "${GREEN}✓ Remote origin added${NC}"
fi

# Step 7: Stage all files
echo -e "${BLUE}Step 7: Staging files...${NC}"
git add .
echo -e "${GREEN}✓ Files staged${NC}"

# Step 8: Show status
echo -e "${BLUE}Step 8: Repository status...${NC}"
git status --porcelain | head -20
total_files=$(git status --porcelain | wc -l)
echo "Total files to be committed: $total_files"

if [ $total_files -eq 0 ]; then
    echo -e "${YELLOW}No changes to commit${NC}"
    exit 0
fi

# Step 9: Create commit
echo -e "${BLUE}Step 9: Creating commit...${NC}"
read -p "Enter commit message (or press Enter for default): " commit_msg
if [ -z "$commit_msg" ]; then
    commit_msg="Upload latest changes from GPU - $(date '+%Y-%m-%d %H:%M:%S')"
fi

git commit -m "$commit_msg"
echo -e "${GREEN}✓ Commit created${NC}"

# Step 10: Check for existing branch
echo -e "${BLUE}Step 10: Checking branch status...${NC}"
current_branch=$(git branch --show-current)
if [ "$current_branch" != "$BRANCH_NAME" ]; then
    if git show-ref --verify --quiet refs/heads/$BRANCH_NAME; then
        echo "Switching to existing $BRANCH_NAME branch"
        git checkout $BRANCH_NAME
    else
        echo "Creating new $BRANCH_NAME branch"
        git checkout -b $BRANCH_NAME
    fi
fi
echo -e "${GREEN}✓ On branch $BRANCH_NAME${NC}"

# Step 11: Fetch remote changes (if repository exists)
echo -e "${BLUE}Step 11: Fetching remote changes...${NC}"
if git ls-remote --exit-code origin &> /dev/null; then
    echo "Repository exists remotely, fetching latest changes..."
    git fetch origin
    
    # Check if remote branch exists
    if git ls-remote --exit-code origin $BRANCH_NAME &> /dev/null; then
        echo "Remote branch exists, checking for conflicts..."
        
        # Check if we need to merge
        LOCAL=$(git rev-parse @)
        REMOTE=$(git rev-parse @{u} 2>/dev/null || git rev-parse origin/$BRANCH_NAME)
        BASE=$(git merge-base @ origin/$BRANCH_NAME 2>/dev/null || echo "")
        
        if [ "$LOCAL" = "$REMOTE" ]; then
            echo "Already up to date"
        elif [ "$LOCAL" = "$BASE" ]; then
            echo "Need to pull"
            git pull origin $BRANCH_NAME
        elif [ "$REMOTE" = "$BASE" ]; then
            echo "Need to push"
        else
            echo -e "${YELLOW}Branches have diverged${NC}"
            echo "You may need to handle merge conflicts"
            read -p "Continue with force push? (y/N): " force_push
            if [[ ! $force_push =~ ^[Yy]$ ]]; then
                echo "Aborting. Please resolve conflicts manually."
                exit 1
            fi
        fi
    fi
    echo -e "${GREEN}✓ Remote changes fetched${NC}"
else
    echo -e "${YELLOW}Repository doesn't exist remotely yet${NC}"
fi

# Step 12: Push to GitHub
echo -e "${BLUE}Step 12: Pushing to GitHub...${NC}"
echo "This will upload your code to: $REPO_URL"
read -p "Proceed with push? (y/N): " confirm_push

if [[ ! $confirm_push =~ ^[Yy]$ ]]; then
    echo "Push cancelled. Your changes are committed locally."
    exit 0
fi

# Attempt to push
echo "Pushing to origin/$BRANCH_NAME..."
if git push -u origin $BRANCH_NAME; then
    echo -e "${GREEN}✓ Successfully pushed to GitHub${NC}"
else
    echo -e "${RED}Push failed. This might be due to:${NC}"
    echo "1. Authentication issues (need to set up SSH key or personal access token)"
    echo "2. No write access to the repository"
    echo "3. Network connectivity issues"
    echo
    echo "To set up authentication:"
    echo "1. SSH key: https://docs.github.com/en/authentication/connecting-to-github-with-ssh"
    echo "2. Personal access token: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token"
    exit 1
fi

# Step 13: Success summary
echo
echo -e "${GREEN}=== Upload Complete! ===${NC}"
echo -e "${GREEN}✓ Repository: $REPO_URL${NC}"
echo -e "${GREEN}✓ Branch: $BRANCH_NAME${NC}"
echo -e "${GREEN}✓ Commit: $commit_msg${NC}"
echo -e "${GREEN}✓ Files uploaded: $total_files${NC}"
echo
echo -e "${BLUE}Next steps:${NC}"
echo "1. Visit your repository: $REPO_URL"
echo "2. Verify your files are uploaded correctly"
echo "3. Create a pull request if needed"
echo "4. Set up CI/CD if required"

# Step 14: Optional - open repository in browser
if command -v xdg-open &> /dev/null; then
    read -p "Open repository in browser? (y/N): " open_browser
    if [[ $open_browser =~ ^[Yy]$ ]]; then
        xdg-open "$REPO_URL"
    fi
elif command -v open &> /dev/null; then
    read -p "Open repository in browser? (y/N): " open_browser
    if [[ $open_browser =~ ^[Yy]$ ]]; then
        open "$REPO_URL"
    fi
fi

echo -e "${GREEN}Script completed successfully!${NC}"
