import { QueryClient } from '@tanstack/react-query'
import { isApiError } from '../contracts/common'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        retry: (failureCount, error) => {
          if (isApiError(error)) return error.retryable && failureCount < 2
          return failureCount < 1
        },
        throwOnError: false,
      },
      mutations: { retry: 0 },
    },
  })
}
