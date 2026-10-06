import { useEffect, useState } from 'react'
import './App.css'

function App() {
  const [measurements, setMeasurements] = useState([])
  const [name, setName] = useState('')
  const [type, setType] = useState('Pressure')
  const [unit, setUnit] = useState('bar')

  // Get measurements from FastAPI when the page loads
  useEffect(() => {
    fetch('http://127.0.0.1:8000/measurements')
      .then(response => response.json())
      .then(data => setMeasurements(data))
  }, [])

  // Send a new measurement to FastAPI
  const addMeasurement = async (event) => {
    event.preventDefault()

    const response = await fetch(
      'http://127.0.0.1:8000/measurements',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: name,
          type: type,
          unit: unit,
        }),
      }
    )

    const newMeasurement = await response.json()

    setMeasurements([...measurements, newMeasurement])

    setName('')
  }

  return (
    <div className="app">
      <h1>Sensor Metadata Management Platform</h1>

      <h2>Add Measurement</h2>

      <form onSubmit={addMeasurement}>
        <div>
          <label>Measurement Name</label>

          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Pressure Sensor 01"
            required
          />
        </div>

        <div>
          <label>Measurement Type</label>

          <select
            value={type}
            onChange={(event) => setType(event.target.value)}
          >
            <option>Pressure</option>
            <option>Flow</option>
            <option>Temperature</option>
            <option>Level</option>
          </select>
        </div>

        <div>
          <label>Measurement Unit</label>

          <select
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
          >
            <option>bar</option>
            <option>L/s</option>
            <option>°C</option>
            <option>m</option>
          </select>
        </div>

        <button type="submit">
          Add Measurement
        </button>
      </form>

      <h2>Measurements</h2>

      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Unit</th>
          </tr>
        </thead>

        <tbody>
          {measurements.map((measurement, index) => (
            <tr key={index}>
              <td>{measurement.name}</td>
              <td>{measurement.type}</td>
              <td>{measurement.unit}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default App