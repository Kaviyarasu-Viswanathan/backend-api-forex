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

const PORT = process.env.PORT || 3001;

// Active client sessions
const clients = new Map();

wss.on('connection', (ws) => {
    console.log('New WebSocket connection');

    clients.set(ws, {
        tvClient: null,
        chart: null,
        symbol: 'OANDA:EURUSD',
        interval: 'D'
    });

    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);
            const client = clients.get(ws);

            console.log('Received message:', data.type);

            if (data.type === 'INIT_CHART') {
                await setupChart(ws, client, data.symbol || 'OANDA:EURUSD', data.interval || 'D');
            }
        } catch (e) {
            console.error('Error processing message:', e);
            ws.send(JSON.stringify({ type: 'ERROR', message: e.message }));
        }
    });

    ws.on('close', () => {
        console.log('Client disconnected');
        const client = clients.get(ws);
        if (client) {
            if (client.chart) {
                try { client.chart.delete(); } catch (e) { }
            }
            if (client.tvClient) {
                try { client.tvClient.end(); } catch (e) { }
            }
        }
        clients.delete(ws);
    });
});

async function setupChart(ws, client, symbol, interval) {
    try {
        // Cleanup old connections
        if (client.chart) {
            try { client.chart.delete(); } catch (e) { }
        }
        if (client.tvClient) {
            try { client.tvClient.end(); } catch (e) { }
        }

        console.log(`Setting up chart for ${symbol} (${interval})`);

        // Create new TradingView Client
        const tvClient = new TradingView.Client();
        client.tvClient = tvClient;

        // Wait for connection
        tvClient.onConnected(() => {
            console.log('TradingView connected');

            const chart = new tvClient.Session.Chart();
            client.chart = chart;

            // Map interval
            let tf = interval;
            if (interval === '1H') tf = '60';
            else if (interval === '4H') tf = '240';
            else if (interval === '1D' || interval === 'D') tf = 'D';

            chart.setMarket(symbol, {
                timeframe: tf,
                range: 100
            });

            chart.onUpdate(() => {
                const periods = chart.periods;
                console.log(`Received ${periods ? periods.length : 0} candles`);

                if (!periods || periods.length === 0) {
                    console.log('No periods data yet');
                    return;
                }

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
                    console.log(`Sent ${candles.length} candles to client`);
                }
            });

            chart.onError((err) => {
                console.error('Chart error:', err);
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: 'ERROR', message: 'Chart error: ' + err }));
                }
            });
        });

        tvClient.onError((err) => {
            console.error('TradingView client error:', err);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ERROR', message: 'Connection error: ' + err }));
            }
        });

        tvClient.onDisconnected(() => {
            console.log('TradingView disconnected');
        });

        client.symbol = symbol;
        client.interval = interval;

    } catch (e) {
        console.error('Setup error:', e);
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ERROR', message: 'Setup failed: ' + e.message }));
        }
    }
}

app.get('/health', (req, res) => {
    res.json({ status: 'ok', service: 'tv-service', connections: clients.size });
});

app.get('/', (req, res) => {
    res.send('TV Service is running. Connect via WebSocket.');
});

server.listen(PORT, () => {
    console.log(`TV Service running on port ${PORT}`);
});
