#!/bin/bash
# 0xLIQD Bot Startup Script
# =====================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to setup environment
setup_environment() {
    echo -e "${BLUE}🔥 0xLIQD Bot Startup${NC}"
    echo "==============================="

    # Check if virtual environment exists
    if [ ! -d "0xliqd" ]; then
        echo -e "${YELLOW}📦 Creating virtual environment...${NC}"
        python3 -m venv 0xliqd
    fi

    # Activate virtual environment
    echo -e "${YELLOW}🔌 Activating virtual environment...${NC}"
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
        source 0xliqd/Scripts/activate 2>/dev/null || {
            echo "Windows detected, using Scripts/activate..."
            0xliqd/Scripts/activate
        }
    else
        source 0xliqd/bin/activate
    fi

    # Install/update dependencies
    echo -e "${YELLOW}⬇️  Installing dependencies...${NC}"
    pip install -r requirements.txt --quiet

    # Create logs directory
    mkdir -p logs
}

# Function to run the bot
run_bot() {
    echo ""
    echo -e "${GREEN}🚀 Starting 0xLIQD Bot...${NC}"
    echo -e "${BLUE}   Press 'r' + Enter to reload${NC}"
    echo -e "${BLUE}   Press 'q' + Enter to quit${NC}"
    echo -e "${BLUE}   Or use Ctrl+C to stop${NC}"
    echo ""

    # Enable job control
    set -m
    
    # Run bot in background
    python3 0xliqd.py &
    BOT_PID=$!
    
    # Function to handle signals
    cleanup() {
        echo -e "\n${YELLOW}🛑 Stopping bot...${NC}"
        kill $BOT_PID 2>/dev/null
        wait $BOT_PID 2>/dev/null
    }
    
    # Set trap for cleanup
    trap cleanup EXIT INT TERM
    
    # Monitor for key presses and bot status
    while kill -0 $BOT_PID 2>/dev/null; do
        if read -t 1 -n 1 key 2>/dev/null; then
            case $key in
                'r'|'R')  # Press 'r' or 'R' to reload
                    echo -e "\n${YELLOW}🔄 Reloading bot...${NC}"
                    kill $BOT_PID 2>/dev/null
                    wait $BOT_PID 2>/dev/null
                    echo -e "${GREEN}✅ Bot stopped, restarting...${NC}"
                    sleep 1
                    return 0  # Return to main loop to restart
                    ;;
                'q'|'Q')  # Press 'q' or 'Q' to quit
                    echo -e "\n${RED}❌ Shutdown requested${NC}"
                    exit 0
                    ;;
            esac
        fi
        sleep 0.1
    done
    
    # If we reach here, the bot process died unexpectedly
    echo -e "\n${RED}💥 Bot process died unexpectedly, restarting...${NC}"
    sleep 2
    return 0
}

# Main execution
main() {
    # Setup environment once
    setup_environment
    
    # Main loop - restart bot when requested
    while true; do
        run_bot
        
        # If we get here, it means reload was requested
        echo -e "${BLUE}🔄 Reloading in 2 seconds...${NC}"
        sleep 2
    done
}

# Handle script interruption
trap 'echo -e "\n${RED}🛑 Script terminated${NC}"; exit 0' INT TERM

# Start the main function
main