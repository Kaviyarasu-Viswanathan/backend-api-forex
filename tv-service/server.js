const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const TradingView = require('@mathieuc/tradingview');
const cors = require('cors');
require('dotenv').config();

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

app.use(cors());
app.use(express.json());

const PORT = 3001;

// Active client sessions
const clients = new Map(); // ws -> { chart: TVChart, indicators: [] }

wss.on('connection', (ws) => {
    console.log('New WebSocket connection');

    // Default session setup
    clients.set(ws, {
        chart: null,
        symbol: 'CURRENCY:EURUSD',
        interval: '1D',
        indicators: []
    });

    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);
            const client = clients.get(ws);

            if (data.type === 'INIT_CHART') {
                await setupChart(ws, client, data.symbol, data.interval);
            } else if (data.type === 'ADD_INDICATOR') {
                // Future: Add indicator logic
            }
        } catch (e) {
            console.error('Error processing message:', e);
        }
    });

    ws.on('close', () => {
        console.log('Client disconnected');
        const client = clients.get(ws);
        if (client && client.chart) {
            client.chart.delete();
        }
        clients.delete(ws);
    });
});

async function setupChart(ws, client, symbol, interval) {
    // Cleanup old chart if exists
    if (client.chart) {
        client.chart.delete();
        client.indicators = [];
    }

    console.log(`Setting up chart for ${symbol} (${interval})`);

    // Create new TradingView Client
    const tvClient = new TradingView.Client();

    // Login if SESSION_ID provided (for premium features)
    if (process.env.TV_SESSION_ID) {
        // tvClient.login(process.env.TV_SESSION_ID); // Library support varies, omitting for now unless needed
    }

    const chart = new tvClient.Session.Chart();

    // Map interval string to TV format
    // 1D -> "1D", 1H -> "60", 4H -> "240"
    let timeframe = interval;
    if (interval === '1H') timeframe = '60';
    if (interval === '4H') timeframe = '240';

    chart.setMarket(symbol, {
        timeframe: timeframe,
        range: 100 // Load last 100 candles
    });

    // Listen for data
    chart.onUpdate(() => {
        if (!client.chart) return; // Disconnected

        const periods = chart.periods;
        if (!periods || periods.length === 0) return;

        // Send full update or delta (sending full for simplicity first)
        const candles = periods.map(p => ({
            time: p.time * 1000,
            open: p.open,
            high: p.max,
            low: p.min,
            close: p.close,
            volume: p.volume
        }));

        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'CHART_DATA',
                data: candles
            }));
        }
    });

    client.chart = chart;
    client.symbol = symbol;
    client.interval = interval;
}

app.get('/health', (req, res) => {
    res.json({ status: 'ok', service: 'tv-service' });
});

server.listen(PORT, () => {
    console.log(`TV Service running on port ${PORT}`);
});
