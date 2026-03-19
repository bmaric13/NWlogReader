```typescript
import type { NextApiRequest, NextApiResponse } from 'next';
import { verify } from 'jsonwebtoken';

const protectedRoute = async (req: NextApiRequest, res: NextApiResponse) => {
  const token = req.headers.authorization;

  if (!token) {
    return res.status(401).json({ message: 'Unauthorized' });
  }

  try {
    const decoded = verify(token, process.env.SECRET_KEY!) as { userId: number };

    res.json({ message: `Hello, user ${decoded.userId}!` });
  } catch (error) {
    res.status(401).json({ message: 'Invalid token' });
  }
};

export default protectedRoute;
```