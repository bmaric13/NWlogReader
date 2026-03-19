```typescript
import type { NextApiRequest, NextApiResponse } from 'next';
import { errorHandler } from '../utils/errorHandler';

const authenticate = async (req: NextApiRequest, res: NextApiResponse) => {
  const token = req.headers.authorization;

  if (!token) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  try {
    // Verify token
    // ...
  } catch (error) {
    return errorHandler(error, req, res);
  }
};

export default authenticate;
```