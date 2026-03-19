```python
import logging

def main():
    try:
        # App logic here
        logging.info('App started successfully')
    except Exception as e:
        logging.error(f'Error starting app: {str(e)}')
        raise

if __name__ == '__main__':
    main()
```