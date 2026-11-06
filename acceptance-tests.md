Feature: Event-Triggered Bitcoin Automation Agent
  As an operator
  I want a Streamlit app with event-driven processing
  So that I can monitor Bitcoin signals, predictions, and trading performance

  Background:
    Given the application is running via Docker Compose
    And RabbitMQ is available
    And the app starts with a paper-trading cash balance of 100000 USD

  Scenario: Price polling runs every 10 seconds
    Given the price scheduler is enabled
    When 30 seconds of runtime elapse
    Then exactly 3 price check cycles should be attempted
    And no cycle should start before the prior cycle has finished

  Scenario: Price lookup retries with exponential backoff on transient failure
    Given the next price lookup fails twice then succeeds
    When the scheduler triggers a price check
    Then the lookup should be attempted 3 times
    And retry delay should increase exponentially between attempts
    And the successful response should be published to the Prices queue

  Scenario: Price lookup failure is dead-lettered after max retries
    Given the next price lookup fails on all retry attempts
    When the scheduler triggers a price check
    Then the lookup should be attempted at most 3 times
    And a failure message with error details should be published to the DeadLetter queue
    And no Movement message should be published to the Prices queue

  Scenario: Price movement messages are published in JSON format
    Given a successful price lookup result is received
    When the result is transformed for queue publication
    Then a valid Movement JSON object should be produced
    And that object should be published to the Prices queue

  Scenario: Price listener computes moving averages from queue messages
    Given Movement messages exist on the Prices queue
    When the listener consumes those messages
    Then moving averages for configured ranges should be calculated
    And calculated averages should be available to downstream prediction logic

  Scenario: News polling runs on the same 10 second schedule
    Given the news scheduler is enabled
    When 30 seconds of runtime elapse
    Then exactly 3 news check cycles should be attempted
    And each cycle should query BBC Business news only

  Scenario: News cache suppresses unchanged story sets
    Given the cached news set matches the latest fetched news set
    When a news check completes
    Then no prediction event should be generated
    And no trade should be executed

  Scenario: News change triggers LLM prediction using full daily context
    Given the latest fetched news set differs from the cached set
    And all current-day stories are available including the latest story
    And the latest Bitcoin price and moving averages are available
    When a prediction is requested
    Then the LLM should be invoked once with all current-day stories
    And the prediction should include a direction of either "up" or "down"
    And the prediction should include a magnitude from 1 to 5

  Scenario: LLM provider and model are configured through OpenRouter
    Given the OPENROUTER_API_KEY environment variable is set
    When a prediction request is issued
    Then the request should be sent via OpenRouter
    And the model "openai/gpt-4.1-mini" should be used
    And no API key value should be persisted to repository files

  Scenario: Prediction magnitude maps to exponential trade sizing
    Given a prediction magnitude of <magnitude>
    When a paper trade is created
    Then the trade notional should equal <expected_notional> USD

    Examples:
      | magnitude | expected_notional |
      | 1         | 100               |
      | 2         | 1000              |
      | 3         | 10000             |
      | 4         | 100000            |
      | 5         | 1000000           |

  Scenario: Chart overlays price, moving averages, and predictions
    Given historical price points and moving averages exist
    And at least one prediction outcome exists
    When the Streamlit chart is rendered
    Then all numerical series should appear on the same chart
    And each prediction marker should be rendered at the bottom of the graph
    And each marker should provide a tooltip with prediction details

  Scenario: Prediction correctness colors are applied on chart markers
    Given a completed prediction with a known actual direction
    When the prediction marker is rendered
    Then the marker should be red when predicted direction matches actual direction
    And the marker should be green when predicted direction does not match actual direction

  Scenario: Paper book tracks P/L over time
    Given multiple predictions and corresponding market outcomes exist
    When trades are settled in sequence
    Then the running Profit/Loss should be updated after each settlement
    And current portfolio value should be displayed in the UI

Feature: Deployment and Runtime Profiles
  As a developer
  I want isolated Docker Compose profiles
  So that I can run either production-like app mode or debug mode

  Scenario: Default app profile runs normal runtime only
    Given Docker Compose is invoked without an explicit profile override
    When services start
    Then only services required for app runtime should be active
    And debug-only services should not be active

  Scenario: Debug profile supports VS Code attach and live edits
    Given Docker Compose is invoked with the debug profile
    When services start
    Then the debug-capable app service should be active
    And source should be mounted for live edits
    And a VS Code launch configuration should allow one-click debugger attach

  Scenario: App and debug profiles are mutually exclusive on one port
    Given the app and debug profiles use the same application port
    When one profile is running
    Then the other profile should not be started simultaneously

Feature: Operability Documentation
  As a user
  I want clear run instructions
  So that I can start the app in both modes reliably

  Scenario: README documents local execution paths
    Given repository documentation is reviewed
    When the README is opened
    Then it should include concise steps for running app mode
    And it should include concise steps for running debug mode

  Scenario: Makefile exposes app and debug targets
    Given the project root contains a Makefile
    When available targets are listed
    Then there should be a target to run app mode
    And there should be a target to run debug mode
