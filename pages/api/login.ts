```typescript
import type { NextApiRequest, NextApiResponse } from 'next';
import axios from 'axios';

const login = async (req: NextApiRequest, res: NextApiResponse) => {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { username, password } = req.body;

  if (!username || !password) {
    return res.status(400).json({ error: 'Invalid request' });
  }

  try {
    const response = await axios.post('https://example.com/login', { username, password });
    if (response.status === 200) {
      return res.status(200).json(response.data);
    } else {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
  } catch (error) {
    console.error(error);
    return res.status(500).json({ error: 'Internal server error' });
  }
};

export default login;
```