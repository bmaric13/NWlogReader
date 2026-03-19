```typescript
import { NextApiRequest, NextApiResponse } from 'next';
import { validateInput } from '../../utils/validateInput';

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  try {
    if (req.method === 'GET') {
      const logId = req.query.logId;
      if (!validateInput(logId as string)) {
        res.status(400).json({ error: 'Invalid logId' });
        return;
      }
      // Fetch log data
      const logData = { id: logId, data: 'Log data' };
      res.json(logData);
    } else {
      res.status(405).json({ error: 'Method not allowed' });
    }
  } catch (error) {
    console.error(error);
    res.status(500).json({ error: 'Internal server error' });
  }
}
```