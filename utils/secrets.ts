```typescript
import { NextApiRequest, NextApiResponse } from 'next';

const secrets = {
  apiKey: process.env.NEXT_PUBLIC_API_KEY,
  secretKey: process.env.SECRET_KEY,
};

export function getSecrets(req: NextApiRequest, res: NextApiResponse) {
  if (!secrets.apiKey || !secrets.secretKey) {
    return res.status(500).json({ error: 'Secrets not configured' });
  }
  return res.json(secrets);
}
```