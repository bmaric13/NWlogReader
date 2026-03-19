```typescript
import { createClient } from 'redis';

const client = createClient({
  socket: {
    port: 6379,
    host: 'localhost',
  },
});

client.on('error', (err) => console.error('Redis error:', err));

export async function getLogs() {
  const logs = await client.get('logs');
  return logs ? JSON.parse(logs) : [];
}

export async function addLog(log: any) {
  const logs = await getLogs();
  logs.push(log);
  await client.set('logs', JSON.stringify(logs));
}
```