```typescript
import type { NextApiRequest, NextApiResponse } from 'next';

const errorHandler = (error: any, req: NextApiRequest, res: NextApiResponse) => {
  console.error(error);

  if (error instanceof Error) {
    return res.status(500).json({ error: error.message });
  } else {
    return res.status(500).json({ error: 'Internal server error' });
  }
};

export default errorHandler;
```