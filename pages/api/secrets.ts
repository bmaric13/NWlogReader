```typescript
import type { NextApiRequest, NextApiResponse } from 'next';
import { getSecrets } from '../../utils/secrets';

const handler = async (req: NextApiRequest, res: NextApiResponse) => {
  if (req.method === 'GET') {
    return getSecrets(req, res);
  }
  return res.status(405).json({ error: 'Method not allowed' });
};

export default handler;
```