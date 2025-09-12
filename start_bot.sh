#!/bin/bash
# 0xLIQD Bot Startup Script
# ==================================

echo "🔥 0xLIQD Bot Startup"
echo "==============================="

# Check if virtual environment exists
if [ ! -d "0xliqd" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv 0xliqd
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source 0xliqd/bin/activate 2>/dev/null || {
    echo "Windows detected, using Scripts/activate..."
    0xliqd/Scripts/activate
}

# Install/update dependencies
echo "⬇️  Installing dependencies..."
pip install -r requirements.txt --quiet

# Create logs directory
mkdir -p logs

echo ""
echo "🚀 Starting 0xLIQD Bot..."
echo "   Press Ctrl+C to stop"
echo ""

# Run the bot
python3 0xliqd.py